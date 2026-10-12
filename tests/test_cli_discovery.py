"""DREAM-246: one installation lookup shared by what is shown and what is spawned.

Every test runs against a temporary HOME and CODEX_HOME and a replaced shutil.which, so no real install, sign-in,
CLI configuration or model cache is looked at, and the only children spawned are the fixture scripts written here
(the nvm tests take every folder holding a real node off PATH first, so the node that runs is the fixture's).
"""
from __future__ import annotations

import os
import shutil
from pathlib import Path
from types import SimpleNamespace

import pytest

from dream.core import cli_discovery
from dream.core.cli_discovery import KNOWN_CLIS, find_cli, locate_command, node_child_env

_REAL_WHICH = shutil.which


@pytest.fixture
def path_hits(monkeypatch):
    """What the fake PATH lookup finds for the provider CLIs, as {name: path}. Empty: none of them is on PATH.
    Other commands (the fixture scripts' own interpreter and the like) are looked up for real."""
    hits: dict[str, str] = {}

    def which(name, *args, **kwargs):
        if name in KNOWN_CLIS:
            return hits.get(name)
        return _REAL_WHICH(name, *args, **kwargs)

    monkeypatch.setattr(shutil, 'which', which)
    return hits


@pytest.fixture
def home(tmp_path, monkeypatch, path_hits):
    """An empty HOME, no NVM_DIR, and a PATH lookup that finds none of the provider CLIs. CODEX_HOME goes to the
    same temporary HOME: the Council choices read the Codex model cache from there, and no real one may be read."""
    root = tmp_path / 'home'
    root.mkdir()
    monkeypatch.setenv('HOME', str(root))
    monkeypatch.setenv('CODEX_HOME', str(root / '.codex'))
    monkeypatch.delenv('NVM_DIR', raising=False)
    return root


def install(folder: Path, name: str, body: str = '#!/bin/sh\nexit 0\n', mode: int = 0o755) -> Path:
    folder.mkdir(parents=True, exist_ok=True)
    target = folder / name
    target.write_text(body)
    target.chmod(mode)
    return target


def nvm_bin(home: Path, version: str) -> Path:
    return home / '.nvm' / 'versions' / 'node' / version / 'bin'


# --- the lookup itself --------------------------------------------------------


def test_nothing_installed_is_none_for_every_known_name(home):
    assert {name: find_cli(name) for name in KNOWN_CLIS} == dict.fromkeys(KNOWN_CLIS)


def test_path_wins_over_a_fallback_folder_and_is_not_required_to_exist(home, path_hits):
    install(home / '.local' / 'bin', 'codex')
    path_hits['codex'] = '/fake/path/bin/codex'
    assert find_cli('codex') == '/fake/path/bin/codex'


@pytest.mark.parametrize('parts', [('.local', 'bin'), ('.grok', 'bin'), ('.npm-global', 'bin'),
                                   ('.nvm', 'versions', 'node', 'v20.11.1', 'bin')])
def test_each_known_user_folder_is_found_when_path_misses_it(home, parts):
    expected = install(home.joinpath(*parts), 'grok')
    assert find_cli('grok') == str(expected)
    assert find_cli('gemini') is None


def test_fixed_folders_come_before_nvm(home):
    install(nvm_bin(home, 'v22.0.0'), 'claude')
    expected = install(home / '.local' / 'bin', 'claude')
    assert find_cli('claude') == str(expected)


def test_nvm_takes_the_highest_stable_version_numerically_and_skips_malformed_names(home):
    for version in ('v9.9.9', 'v10.2.0', 'v10.11.1', 'v22.0.0-rc.1', 'v99', 'latest', '10.99.0', 'v10.11.1.5'):
        install(nvm_bin(home, version), 'codex')
    (home / '.nvm' / 'versions' / 'node' / 'v98.0.0').write_text('a file, not a version folder')
    assert find_cli('codex') == str(nvm_bin(home, 'v10.11.1') / 'codex')


def test_nvm_falls_to_the_next_version_that_actually_has_the_cli(home):
    nvm_bin(home, 'v22.3.0').mkdir(parents=True)
    install(nvm_bin(home, 'v20.1.0'), 'gemini')
    install(nvm_bin(home, 'v18.20.4'), 'gemini')
    assert find_cli('gemini') == str(nvm_bin(home, 'v20.1.0') / 'gemini')


def test_nvm_dir_is_used_only_as_an_absolute_explicit_root(home, tmp_path, monkeypatch):
    default = install(nvm_bin(home, 'v20.0.0'), 'codex')
    custom = install(tmp_path / 'nvm-elsewhere' / 'versions' / 'node' / 'v18.0.0' / 'bin', 'codex')
    monkeypatch.setenv('NVM_DIR', str(tmp_path / 'nvm-elsewhere'))
    assert find_cli('codex') == str(custom)
    monkeypatch.setenv('NVM_DIR', 'relative/nvm')
    assert find_cli('codex') == str(default)


def test_a_fallback_must_be_an_executable_regular_file(home):
    install(home / '.local' / 'bin', 'codex', mode=0o644)             # not executable
    (home / '.grok' / 'bin' / 'codex').mkdir(parents=True)            # a folder with the name
    (home / '.npm-global' / 'bin').mkdir(parents=True)
    (home / '.npm-global' / 'bin' / 'codex').symlink_to(home / 'gone')  # dangling link
    assert find_cli('codex') is None
    expected = install(nvm_bin(home, 'v20.0.0'), 'codex')
    assert find_cli('codex') == str(expected)


@pytest.mark.parametrize('name', ['python3', 'sh', '', 'codex --yolo', '../codex', '/bin/sh', 'Codex', 'codex\n',
                                  None, 7, ['codex']])
def test_unknown_names_are_refused_before_any_lookup(home, monkeypatch, name):
    looked = []
    monkeypatch.setattr(shutil, 'which', lambda *a, **k: looked.append(a) or '/bin/sh')
    monkeypatch.setattr(cli_discovery.os, 'scandir', lambda *a, **k: looked.append(a) or iter(()))
    with pytest.raises(ValueError):
        find_cli(name)
    assert looked == []


def test_other_commands_get_path_lookup_only_never_the_fallback_folders(home, tmp_path):
    install(home / '.local' / 'bin', 'some-other-cli')
    assert locate_command('some-other-cli') is None
    assert locate_command('') is None and locate_command(None) is None
    explicit = install(tmp_path / 'explicit', 'codex')
    assert locate_command(str(explicit)) == str(explicit)            # an adapter pointed at a path, as before
    known = install(home / '.grok' / 'bin', 'grok')
    assert locate_command('grok') == str(known)


def test_filesystem_errors_end_the_lookup_without_raising(home, monkeypatch):
    install(nvm_bin(home, 'v20.0.0'), 'codex')

    def denied(path, *args, **kwargs):
        raise PermissionError(13, 'denied', str(path))

    monkeypatch.setattr(cli_discovery.os, 'scandir', denied)
    assert find_cli('codex') is None
    monkeypatch.setattr(cli_discovery.os, 'access', denied)
    install(home / '.local' / 'bin', 'codex')
    assert find_cli('codex') is None


def test_the_nvm_listing_is_bounded(home, monkeypatch):
    for patch in range(6):
        install(nvm_bin(home, f'v1.0.{patch}'), 'codex')
    monkeypatch.setattr(cli_discovery, '_MAX_NODE_ENTRIES', 0)
    assert find_cli('codex') is None


def test_only_the_nvm_versions_folder_is_listed_and_nothing_is_changed(home, monkeypatch):
    install(nvm_bin(home, 'v20.0.0'), 'codex')
    (home / '.codex').mkdir()
    (home / 'Documents').mkdir()
    listed = []
    real_scandir = os.scandir

    def scandir(path='.'):
        listed.append(str(path))
        return real_scandir(path)

    environment = dict(os.environ)
    before = sorted(str(p) for p in home.rglob('*'))
    monkeypatch.setattr(cli_discovery.os, 'scandir', scandir)
    for name in KNOWN_CLIS:
        find_cli(name)
    during_lookup = set(listed)          # taken now: the rglob below lists folders through os.scandir too
    assert during_lookup == {str(home / '.nvm' / 'versions' / 'node')}
    assert dict(os.environ) == environment
    assert sorted(str(p) for p in home.rglob('*')) == before


# --- what is found is what is spawned ----------------------------------------

_RECORDING_CODEX = """#!/usr/bin/env bash
printf '%s' "$0" > "{record}/argv0"
printf '%s' "$*" > "{record}/args"
{stdin}
echo '{{"type":"thread.started","thread_id":"fixture-thread"}}'
echo '{{"type":"item.completed","item":{{"type":"agent_message","text":"fixture answer"}}}}'
echo '{{"type":"turn.completed","usage":{{}}}}'
"""


async def test_main_cli_backend_spawns_the_fallback_file_that_was_found(home, tmp_path):
    from dream.core.backends.cli_agent import CliAgentBackend, CodexAdapter

    record = tmp_path / 'record'
    record.mkdir()
    # The main path carries the prompt on stdin.
    fake = install(nvm_bin(home, 'v20.1.0'), 'codex', _RECORDING_CODEX.format(record=record, stdin=f'cat > "{record}/payload"'))
    adapter = CodexAdapter()
    backend = CliAgentBackend(adapter, system_prompt='SYSTEM', cwd=str(tmp_path), model='fixture-model',
                              sandbox_getter=lambda: 'read-only')
    await backend.connect()
    events = [event async for event in backend.ask('ping')]
    await backend.disconnect()

    assert [event.kind for event in events] == ['assistant_done', 'result']
    assert (record / 'argv0').read_text() == find_cli('codex') == str(fake)
    # Options, sandbox and prompt are where they were; only the program is pinned.
    args = (record / 'args').read_text()
    assert args.startswith('exec --model fixture-model --json')
    assert '--sandbox read-only' in args and args.endswith(' -')
    assert (record / 'payload').read_text() == 'SYSTEM\n\nping'
    # The adapter itself still builds command names.
    assert adapter.argv('p', cwd='/w', resume_id=None, sandbox='read-only')[0] == 'codex'


async def test_main_cli_backend_refuses_to_connect_when_the_cli_is_nowhere(home, tmp_path):
    from dream.core.backends.cli_agent import CliAgentBackend, CodexAdapter

    install(home / '.local' / 'bin', 'codex', mode=0o600)             # present but not executable
    backend = CliAgentBackend(CodexAdapter(), system_prompt='S', cwd=str(tmp_path))
    with pytest.raises(RuntimeError, match='not found'):
        await backend.connect()


async def test_consultation_spawns_the_fallback_file_that_was_found(home, tmp_path):
    from dream.core.cli_review import CLIConsultation, CLIIsolationError

    record = tmp_path / 'record'
    record.mkdir()
    workspace = tmp_path / 'workspace'
    workspace.mkdir()
    provider = SimpleNamespace(key='codex', cli_cmd='codex')
    missing = CLIConsultation(provider, cwd=workspace)
    with pytest.raises(CLIIsolationError, match='not installed'):
        missing.prepare()

    # A consultation sends its prompt on stdin; the fixture drains it.
    fake = install(home / '.npm-global' / 'bin', 'codex',
                   _RECORDING_CODEX.format(record=record, stdin='cat > /dev/null'))
    consultation = CLIConsultation(provider, 'fixture-model', cwd=workspace)
    consultation.prepare()
    try:
        assert await consultation.run('advise') == 'fixture answer'
    finally:
        consultation.close()
    spawned = Path((record / 'argv0').read_text())
    assert spawned == Path(find_cli('codex')).resolve() == fake.resolve()
    assert '--model fixture-model' in (record / 'args').read_text()


# --- an nvm install: an `env node` script whose node the launcher's PATH lacks ----------------------------------

# The CLI file as npm installs it. Nothing below the first line is ever run: the fake node beside it reads none of it.
_ENV_NODE_CLI = '#!/usr/bin/env node\n// fixture only: stands in for an installed CLI entry script\n'

# Stands in for node. It uses shell builtins only, so it needs nothing from PATH. It records the program and
# arguments it was started with and what it could see of the environment, then answers as the Codex CLI would.
_FAKE_NODE = """#!/bin/sh
printf '%s' "$1" > "RECORD/program"
shift
printf '%s' "$*" > "RECORD/args"
printf '%s' "$PATH" > "RECORD/path"
printf '%s' "${DREAM_FIXTURE_AMBIENT-unset}" > "RECORD/ambient"
printf '%s' "${DREAM_FIXTURE_ALLOWED-unset}" > "RECORD/allowed"
DRAIN
printf '%s\\n' '{"type":"thread.started","thread_id":"fixture-thread"}'
printf '%s\\n' '{"type":"item.completed","item":{"type":"agent_message","text":"fixture answer"}}'
printf '%s\\n' '{"type":"turn.completed","usage":{}}'
"""
_DRAIN_STDIN = 'while IFS= read -r _line; do :; done'
_NO_NODE_HERE = '/nonexistent-fixture-bin'


def fake_node(record: Path, *, drain_stdin: bool = False) -> str:
    return _FAKE_NODE.replace('RECORD', str(record)).replace('DRAIN', _DRAIN_STDIN if drain_stdin else ':')


def launcher_path() -> str:
    """The real PATH without any folder that holds a node: what a desktop launcher without nvm gives a child.
    A real node is therefore never started by these tests, whatever is installed on the machine running them."""
    folders = [folder for folder in os.environ.get('PATH', os.defpath).split(os.pathsep)
               if folder and os.path.isabs(folder) and not os.path.lexists(os.path.join(folder, 'node'))]
    assert folders, 'every PATH folder on this machine holds a node; the fixture cannot model a launcher PATH'
    return os.pathsep.join(folders)


def test_child_env_is_a_copy_with_only_the_found_folder_added_to_path(home, monkeypatch):
    folder = nvm_bin(home, 'v20.1.0')
    cli = install(folder, 'codex', _ENV_NODE_CLI)
    install(folder, 'node')
    monkeypatch.setenv('DREAM_FIXTURE_AMBIENT', 'ambient')
    monkeypatch.setenv('PATH', _NO_NODE_HERE)
    ambient = dict(os.environ)

    # A caller's restricted mapping: the result has exactly its names, nothing inherited from the parent.
    provided = {'PATH': '/restricted-fixture-bin', 'HOME': str(home), 'DREAM_FIXTURE_ALLOWED': 'kept'}
    snapshot = dict(provided)
    child = node_child_env('codex', str(cli), provided)
    assert child == {**snapshot, 'PATH': str(folder) + os.pathsep + '/restricted-fixture-bin'}
    assert child is not provided and provided == snapshot

    # No mapping given: the parent's environment, copied, with the same one change.
    inherited = node_child_env('codex', str(cli))
    assert inherited == {**ambient, 'PATH': str(folder) + os.pathsep + _NO_NODE_HERE}
    assert dict(os.environ) == ambient

    # No PATH in the mapping means the system default search, which is kept behind the folder; an empty PATH
    # gets no second (working directory) entry.
    monkeypatch.setattr(os, 'defpath', _NO_NODE_HERE)
    assert node_child_env('codex', str(cli), {'HOME': str(home)}) == {
        'HOME': str(home), 'PATH': str(folder) + os.pathsep + _NO_NODE_HERE}
    assert node_child_env('codex', str(cli), {'PATH': ''}) == {'PATH': str(folder)}

    # `env -S node <flags>` is the same interpreter.
    flagged = install(nvm_bin(home, 'v18.0.0'), 'gemini', '#!/usr/bin/env -S node --no-warnings\n')
    install(nvm_bin(home, 'v18.0.0'), 'node')
    assert node_child_env('gemini', str(flagged), {'PATH': _NO_NODE_HERE})['PATH'].startswith(
        str(nvm_bin(home, 'v18.0.0')) + os.pathsep)


def test_child_env_is_left_alone_whenever_the_node_folder_is_not_needed_or_not_qualified(home, tmp_path):
    env = {'PATH': _NO_NODE_HERE}
    folder = nvm_bin(home, 'v20.1.0')
    cli = install(folder, 'codex', _ENV_NODE_CLI)
    assert node_child_env('codex', str(cli), env) is None                # no node beside it
    node = install(folder, 'node', mode=0o644)
    assert node_child_env('codex', str(cli), env) is None                # a node the user may not execute
    node.chmod(0o755)
    assert node_child_env('codex', str(cli), env) is not None            # the qualifying case, for contrast

    # The child's PATH already finds a node: nothing is put in front of it.
    install(tmp_path / 'other-bin', 'node')
    assert node_child_env('codex', str(cli), {'PATH': f'{_NO_NODE_HERE}{os.pathsep}{tmp_path / "other-bin"}'}) is None

    # Not an `env node` script: a shell script, a node named by path, another interpreter, no interpreter line,
    # or an interpreter line longer than what is read.
    for body in ('#!/bin/sh\nexit 0\n', '#!/usr/bin/node\n', '#!/usr/bin/env nodejs\n', '#!/usr/bin/env\n',
                 '// #!/usr/bin/env node\n', '', '#!/usr/bin/env ' + ' ' * 300 + 'node\n'):
        cli.write_text(body)
        assert node_child_env('codex', str(cli), env) is None, body[:40]
    cli.write_text(_ENV_NODE_CLI)

    # Only the file the lookup found for that known name: no other name, no other file, no relative path.
    other = install(folder, 'some-other-cli', _ENV_NODE_CLI)
    for name, found in (('some-other-cli', str(other)), ('codex', str(other)), ('grok', str(cli)),
                        ('codex --yolo', str(cli)), ('codex', 'codex'), ('codex', None), (None, str(cli)),
                        ('codex', str(folder / 'missing' / 'codex'))):
        assert node_child_env(name, found, env) is None, (name, found)
    assert env == {'PATH': _NO_NODE_HERE}


def test_child_env_reads_only_the_start_of_the_found_cli_file(home, monkeypatch):
    folder = nvm_bin(home, 'v20.1.0')
    cli = install(folder, 'codex', _ENV_NODE_CLI + 'x' * 100_000)
    install(folder, 'node')
    (home / '.codex').mkdir()
    (home / '.codex' / 'auth.json').write_text('{"synthetic":"fixture-only"}')
    opened, sizes = [], []
    real_open, real_read = os.open, os.read

    def recording_open(path, *args, **kwargs):
        opened.append(str(path))
        return real_open(path, *args, **kwargs)

    def recording_read(fd, size):
        sizes.append(size)
        return real_read(fd, size)

    with monkeypatch.context() as patch:
        patch.setattr(cli_discovery.os, 'open', recording_open)
        patch.setattr(cli_discovery.os, 'read', recording_read)
        child = node_child_env('codex', str(cli), {'PATH': _NO_NODE_HERE})
    assert child is not None
    assert opened == [str(cli)] and sizes and max(sizes) <= 256


async def test_main_cli_backend_launches_an_nvm_cli_whose_node_the_launcher_path_lacks(home, tmp_path, monkeypatch):
    from dream.core.backends.cli_agent import CliAgentBackend, CodexAdapter

    record = tmp_path / 'record'
    record.mkdir()
    folder = nvm_bin(home, 'v20.1.0')
    cli = install(folder, 'codex', _ENV_NODE_CLI)
    install(folder, 'node', fake_node(record))
    path = launcher_path()
    monkeypatch.setenv('PATH', path)
    monkeypatch.setenv('DREAM_FIXTURE_AMBIENT', 'ambient')
    ambient = dict(os.environ)

    backend = CliAgentBackend(CodexAdapter(), system_prompt='SYSTEM', cwd=str(tmp_path), model='fixture-model',
                              sandbox_getter=lambda: 'read-only')
    await backend.connect()
    events = [event async for event in backend.ask('ping')]
    await backend.disconnect()

    # The turn completed: the kernel found a node for the `env node` script, and it was the one beside the CLI.
    assert [event.kind for event in events] == ['assistant_done', 'result']
    assert events[-1].data['is_error'] is False
    assert (record / 'program').read_text() == find_cli('codex') == str(cli)
    args = (record / 'args').read_text()
    assert args.startswith('exec --model fixture-model --json')
    assert '--sandbox read-only' in args and args.endswith(' -')
    # The child got the owner's environment plus that one folder; the parent's own environment is untouched.
    assert (record / 'path').read_text() == str(folder) + os.pathsep + path
    assert (record / 'ambient').read_text() == 'ambient'
    assert dict(os.environ) == ambient


async def test_consultation_keeps_a_restricted_env_restricted_when_adding_the_node_folder(home, tmp_path, monkeypatch):
    from dream.core.cli_review import CLIConsultation, runner_env

    record = tmp_path / 'record'
    record.mkdir()
    workspace = tmp_path / 'workspace'
    workspace.mkdir()
    # As npm installs under nvm: bin/codex is a link to the package's entry script; node sits beside the link.
    version = home / '.nvm' / 'versions' / 'node' / 'v20.1.0'
    script = install(version / 'lib' / 'node_modules' / 'fixture-codex' / 'bin', 'codex.js', _ENV_NODE_CLI)
    folder = version / 'bin'
    folder.mkdir(parents=True)
    (folder / 'codex').symlink_to(script)
    install(folder, 'node', fake_node(record, drain_stdin=True))

    path = launcher_path()
    monkeypatch.setenv('PATH', path)
    monkeypatch.setenv('DREAM_FIXTURE_AMBIENT', 'ambient')
    monkeypatch.setenv('DREAM_FIXTURE_ALLOWED', 'kept')
    ambient = dict(os.environ)
    # The allowlisted environment an isolated runner gets, with one named variable let through.
    provided = runner_env(('DREAM_FIXTURE_ALLOWED',))
    assert 'DREAM_FIXTURE_AMBIENT' not in provided and provided['PATH'] == path
    snapshot = dict(provided)

    consultation = CLIConsultation(SimpleNamespace(key='codex', cli_cmd='codex'), 'fixture-model', cwd=workspace)
    consultation.prepare()
    consultation.env = provided
    try:
        assert await consultation.run('advise') == 'fixture answer'
    finally:
        consultation.close()

    # argv[0] is still the resolved script; the node used is the one beside the link that was found.
    assert find_cli('codex') == str(folder / 'codex')
    assert Path((record / 'program').read_text()) == script.resolve()
    assert '--model fixture-model' in (record / 'args').read_text()
    assert (record / 'path').read_text() == str(folder) + os.pathsep + path
    # Nothing the restricted mapping left out reached the child, and what it let through did.
    assert (record / 'ambient').read_text() == 'unset'
    assert (record / 'allowed').read_text() == 'kept'
    assert consultation.env is provided and provided == snapshot
    assert dict(os.environ) == ambient


# --- what is shown ------------------------------------------------------------


def test_council_choices_show_a_fallback_install_without_any_sign_in_file(home):
    from dream.core.council_config import provider_choices

    install(home / '.grok' / 'bin', 'grok')
    assert not (home / '.grok' / 'auth.json').exists()
    choices = {item['key']: item for item in provider_choices()}
    assert choices['grok']['available'] is True
    assert 'sign-in not checked' in choices['grok']['note']
    assert choices['codex']['available'] is False and choices['antigravity']['available'] is False
    assert 'gemini' not in choices
