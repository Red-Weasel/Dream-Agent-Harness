"""DREAM-202 (after DREAM-187): a Sleepwalk run's private folder is made under $XDG_RUNTIME_DIR (/run/user/<uid>)
when that directory is trustworthy -- this uid's, mode exactly 0700 (special bits included), under parents that are
root's or this uid's and not group or other writable, not inside a git repository -- else under dream/sleepwalk-runs
in the user's cache directory ($XDG_CACHE_HOME, else ~/.cache), made 0700 and held to the same rules; never under the
shared system temp directory, where anyone's .git (a Codex sandbox left /tmp/.git behind, 2026-09-29) refused every
run. With neither root usable the run is refused, its record naming what is wrong with each.
Fakes only: the runner is stubbed; .git is planted inside this test's own tmp_path, never in /tmp. pytest's tmp_path
lives under the shared /tmp, which the parent rule rejects by design, so runner._stat is pointed at a view in which
the parents above tmp_path are root's 0755 directories (as /run/user and /run are); and the production root selection,
captured when this module is collected, is put back here (conftest gives every other test a root of its own). The
owner's real /run/user/<uid> is only read, never run in."""
import os
import stat
import tempfile
from pathlib import Path

import pytest

from dream.core import council_config, moe
from dream.sleepwalk import runner, store

CHOICES = [{'key': k, 'label': k, 'available': True, 'efforts': [], 'models': []} for k in ('codex', 'xai')]
REFUSED = '[Sleepwalk: unavailable — no private folder for the run (the shared system temp directory is never used): '
PRODUCTION_ROOTS = runner._ROOTS    # read at import, while collecting: before conftest's autouse fixture replaces it


@pytest.fixture(autouse=True)
def home(monkeypatch):
    monkeypatch.setattr(council_config, 'provider_choices', lambda **_: CHOICES)


@pytest.fixture(autouse=True)
def trusted_parents(tmp_path, monkeypatch):
    """runner._stat sees the parents above tmp_path as root's 0755 directories; tmp_path itself reads as it is (0700,
    pytest's doing), and so does everything below it."""
    real, above = os.stat, set(tmp_path.parents)

    def view(path):
        info, path = real(path), Path(path)
        if path not in above:
            return info
        return os.stat_result((stat.S_IFDIR | 0o755, info.st_ino, info.st_dev, info.st_nlink, 0, info.st_gid, info.st_size,
                               int(info.st_atime), int(info.st_mtime), int(info.st_ctime)))
    monkeypatch.setattr(runner, '_stat', view)


@pytest.fixture(autouse=True)
def cache_root(tmp_path, monkeypatch):
    """The production root selection as collected (conftest replaces it for every other test), no runtime directory
    unless the test sets one, and the cache directory under this test's own tmp_path -> the run root made there."""
    monkeypatch.setattr(runner, '_ROOTS', PRODUCTION_ROOTS)
    monkeypatch.delenv('XDG_RUNTIME_DIR', raising=False)
    monkeypatch.setenv('XDG_CACHE_HOME', str(tmp_path / 'cache'))
    return tmp_path / 'cache' / 'dream' / 'sleepwalk-runs'


@pytest.fixture(autouse=True)
def system_tmp(tmp_path, monkeypatch):
    """The system temp directory is this test's own folder, with the .git a Codex sandbox left in /tmp planted in it;
    and every folder tempfile makes is recorded (the dir each was asked for), so a run that went to the system temp
    directory -- dir None, or under it -- is caught although the folder is removed after the run -> (folder, made)."""
    folder = tmp_path / 'tmp'
    folder.mkdir()
    (folder / '.git').mkdir()
    monkeypatch.setattr(tempfile, 'tempdir', str(folder))
    real, made = tempfile.mkdtemp, []

    def record(suffix=None, prefix=None, dir=None):
        made.append(None if dir is None else Path(dir))
        return real(suffix, prefix, dir)
    monkeypatch.setattr(tempfile, 'mkdtemp', record)
    return folder, made


def make(path, mode=0o700):
    """A directory with exactly this mode (mkdir's is masked by the umask)."""
    path.mkdir()
    path.chmod(mode)
    return path


def runtime(parent, monkeypatch):
    """$XDG_RUNTIME_DIR: a 0700 directory of this user's under `parent`."""
    root = make(parent / 'run')
    monkeypatch.setenv('XDG_RUNTIME_DIR', str(root))
    return root


def foreign(monkeypatch, *paths):
    """runner._stat sees each of `paths` as another uid's, with its real mode."""
    real, others = runner._stat, {Path(p) for p in paths}

    def view(path):
        info = real(path)
        if Path(path) not in others:
            return info
        return os.stat_result((info.st_mode, info.st_ino, info.st_dev, info.st_nlink, os.getuid() + 1, info.st_gid,
                               info.st_size, int(info.st_atime), int(info.st_mtime), int(info.st_ctime)))
    monkeypatch.setattr(runner, '_stat', view)


def vanishing(monkeypatch, root):
    """The root vanished after its check: no folder can be made in it."""
    real = tempfile.mkdtemp

    def gone(suffix=None, prefix=None, dir=None):
        if dir is not None and Path(dir) == root:
            raise FileNotFoundError(2, 'No such file or directory', str(dir))
        return real(suffix, prefix, dir)
    monkeypatch.setattr(tempfile, 'mkdtemp', gone)


def stub(monkeypatch):
    seen = {}

    async def consult(provider, question, context='', cwd=None, **kw):
        seen['cwd'] = cwd
        return 'Done.'
    monkeypatch.setattr(moe, 'consult_advisor', consult)
    return seen


async def run():
    auto = store.save({'title': 'Notes', 'icon': 'sun', 'instructions': 'Say hello.',
                       'triggers': [{'every': 'day', 'at': '07:30'}], 'runner': {'provider': 'codex'}})
    return await runner.run(store.get(auto['id']))


def never_the_system_temp(system_tmp):
    """No folder was asked of the system temp directory (dir None) or made under it, and nothing lies there but the
    planted .git."""
    folder, made = system_tmp
    assert all(d is not None and folder != d and folder not in d.parents for d in made), made
    assert [p.name for p in folder.iterdir()] == ['.git']


def ran_under(record, seen, root, system_tmp):
    """The run happened in a fresh dream-sleepwalk-* folder under `root`, removed afterwards; the last folder tempfile
    made was asked for there, and none of the system temp directory."""
    assert record['status'] == 'OK', record.get('error')
    work = Path(seen['cwd'])
    assert work.parent == root and work.name.startswith('dream-sleepwalk-') and not work.exists()
    assert system_tmp[1] and system_tmp[1][-1] == root, system_tmp[1]
    never_the_system_temp(system_tmp)


def refused(record, seen, system_tmp, *reasons):
    """The run did not happen: recorded FAILED with no output, its error naming each location and what is wrong with
    it; no folder was made anywhere -- the system temp directory included."""
    assert record['status'] == 'FAILED' and record['output'] == '' and 'cwd' not in seen, record
    assert record['error'].startswith(REFUSED), record['error']
    for reason in reasons:
        assert reason in record['error'], (reason, record['error'])
    assert system_tmp[1] == [], system_tmp[1]
    never_the_system_temp(system_tmp)


# --- the production selection: the runtime directory first, then the cache root, and nothing else -----------------

def test_the_production_roots_are_the_runtime_directory_then_the_cache_root():
    """Every test here runs on PRODUCTION_ROOTS, the module's own tuple as it stood when collected -- so a reordered
    tuple, or one with another root (the system temp directory, say) after the two, fails here and in the runs below."""
    assert PRODUCTION_ROOTS == (runner._runtime_root, runner._cache_root)


# --- the runtime directory, when it can be trusted ----------------------------------------------------------------

async def test_a_git_planted_in_the_system_temp_no_longer_refuses_a_run(tmp_path, monkeypatch, system_tmp, cache_root):
    root = runtime(tmp_path, monkeypatch)
    seen = stub(monkeypatch)
    ran_under(await run(), seen, root, system_tmp)
    assert list(root.iterdir()) == []                                     # the run folder is removed after the run
    assert not cache_root.exists()                                        # the fallback is not made while the runtime directory serves


def test_the_real_runtime_directory_passes_the_trust_rules_when_it_is_the_owners(monkeypatch, system_tmp):
    """Read-only: the owner's real /run/user/<uid> passes the trust rules, on its real parents, and would be the run
    root -- nothing is run or made there. The run itself is the test above, on a private runtime directory."""
    real = Path(f'/run/user/{os.getuid()}')
    try:
        info = os.stat(real)
    except OSError:
        info = None
    if info is None or not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid() or stat.S_IMODE(info.st_mode) != 0o700:
        pytest.skip(f"{real} is not this user's 0700 runtime directory on this machine (no logind session?)")
    monkeypatch.setattr(runner, '_stat', os.stat)                         # /run/user and /run as they are
    monkeypatch.setenv('XDG_RUNTIME_DIR', str(real))
    assert runner._untrusted(real) is None
    assert runner._runtime_root() == (real, '')
    assert system_tmp[1] == []                                            # no folder was made, there or anywhere


# --- the cache root, when the runtime directory cannot be used ----------------------------------------------------

UNUSABLE_RUNTIME = ['unset', 'group-bits', 'other-bits', 'special-bit', 'symlink', 'relative', 'missing', 'file', 'foreign',
                    'no-owner-write', 'no-owner-search', 'parent-shared-like-tmp', 'parent-group-writable', 'parent-foreign',
                    'inside-a-repository', 'vanished']
RUNTIME_REASON = {                                                        # what the refusal says of each, when it says
    'unset': '$XDG_RUNTIME_DIR is not set', 'group-bits': 'mode 0770', 'other-bits': 'mode 0705', 'special-bit': 'mode 1700',
    'symlink': 'not an absolute, link-free path', 'relative': 'not an absolute, link-free path', 'missing': 'No such file or directory',
    'file': 'not a directory', 'foreign': f'uid {os.getuid() + 1}', 'no-owner-write': 'mode 0500', 'no-owner-search': 'mode 0600',
    'parent-shared-like-tmp': 'mode 1777', 'parent-group-writable': 'mode 0775', 'parent-foreign': f'uid {os.getuid() + 1}',
    'inside-a-repository': 'inside a git repository', 'vanished': 'no folder can be made there'}


def unusable_runtime(case, tmp_path, monkeypatch):
    """$XDG_RUNTIME_DIR arranged so that it cannot be trusted, or used, in the way `case` names -> its directory
    (None when there is none)."""
    if case == 'unset':
        return None
    if case.startswith('parent-'):
        parent = make(tmp_path / 'parent', 0o755)
        if case == 'parent-foreign':
            foreign(monkeypatch, parent)                                  # another user's home folder, say
        else:
            parent.chmod({'parent-shared-like-tmp': 0o1777, 'parent-group-writable': 0o775}[case])
        return runtime(parent, monkeypatch)
    if case == 'inside-a-repository':
        repo = make(tmp_path / 'repo', 0o755)                             # a trusted parent, apart from its .git
        (repo / '.git').mkdir()
        return runtime(repo, monkeypatch)
    root = runtime(tmp_path, monkeypatch)
    if case in ('group-bits', 'other-bits', 'special-bit', 'no-owner-write', 'no-owner-search'):
        root.chmod({'group-bits': 0o770, 'other-bits': 0o705, 'special-bit': 0o1700, 'no-owner-write': 0o500, 'no-owner-search': 0o600}[case])
    elif case == 'symlink':
        (tmp_path / 'link').symlink_to(root)
        monkeypatch.setenv('XDG_RUNTIME_DIR', str(tmp_path / 'link'))
    elif case == 'relative':
        monkeypatch.chdir(tmp_path)                                       # 'run' exists from here, and is 0700
        monkeypatch.setenv('XDG_RUNTIME_DIR', 'run')
    elif case == 'missing':
        monkeypatch.setenv('XDG_RUNTIME_DIR', str(tmp_path / 'absent'))
    elif case == 'file':
        (tmp_path / 'file').touch()
        (tmp_path / 'file').chmod(0o700)                                  # a 0700 file: only the directory rule refuses it
        monkeypatch.setenv('XDG_RUNTIME_DIR', str(tmp_path / 'file'))
    elif case == 'foreign':
        foreign(monkeypatch, root)                                        # another uid's, 0700
    elif case == 'vanished':
        vanishing(monkeypatch, root)
    return root


@pytest.mark.parametrize('case', UNUSABLE_RUNTIME)
async def test_without_a_usable_runtime_directory_the_run_goes_to_the_cache_root_never_the_system_temp(tmp_path, monkeypatch, system_tmp,
                                                                                                        cache_root, case):
    root = unusable_runtime(case, tmp_path, monkeypatch)
    seen = stub(monkeypatch)
    try:
        record = await run()
    finally:
        if root is not None and root.is_dir():
            root.chmod(0o700)
    ran_under(record, seen, cache_root, system_tmp)
    for folder in (cache_root, cache_root.parent, cache_root.parent.parent):
        assert stat.S_IMODE(folder.stat().st_mode) == 0o700, folder     # each made by the runner, 0700
    if root is not None and root.is_dir():
        assert list(root.iterdir()) == []                                 # nothing was made in the runtime directory


@pytest.mark.parametrize('cache_home', [None, 'relative/cache'], ids=['unset', 'relative'])
async def test_the_cache_root_is_under_dot_cache_in_the_home_folder_by_default(tmp_path, monkeypatch, system_tmp, cache_home):
    """No $XDG_CACHE_HOME, or a relative one (which the XDG specification says to ignore): ~/.cache/dream/sleepwalk-runs."""
    home = make(tmp_path / 'home', 0o750)
    monkeypatch.setenv('HOME', str(home))
    if cache_home is None:
        monkeypatch.delenv('XDG_CACHE_HOME')
    else:
        monkeypatch.setenv('XDG_CACHE_HOME', cache_home)
    seen = stub(monkeypatch)
    ran_under(await run(), seen, home / '.cache' / 'dream' / 'sleepwalk-runs', system_tmp)
    assert stat.S_IMODE((home / '.cache').stat().st_mode) == 0o700


async def test_a_linked_cache_directory_is_taken_by_its_real_path(tmp_path, monkeypatch, system_tmp):
    real = make(tmp_path / 'realcache')
    (tmp_path / 'cache').symlink_to(real)                                 # $XDG_CACHE_HOME is the link
    seen = stub(monkeypatch)
    ran_under(await run(), seen, real / 'dream' / 'sleepwalk-runs', system_tmp)


async def test_an_existing_cache_root_of_the_users_is_used_as_it_is(tmp_path, monkeypatch, system_tmp, cache_root):
    for folder in (cache_root.parent.parent, cache_root.parent, cache_root):
        make(folder)
    (cache_root / 'left-behind').mkdir()                                  # whatever an earlier run left: not touched
    seen = stub(monkeypatch)
    record = await run()
    assert record['status'] == 'OK', record.get('error')
    assert Path(seen['cwd']).parent == cache_root and [p.name for p in cache_root.iterdir()] == ['left-behind']
    never_the_system_temp(system_tmp)


# --- the cache root is held to the same rules: with neither usable, the run is refused ----------------------------

CACHE_CASES = ['parent-group-writable', 'parent-shared-like-tmp', 'parent-foreign', 'inside-a-repository', 'root-not-0700',
               'root-special-bit', 'root-foreign', 'root-a-link', 'root-a-file', 'cannot-be-made', 'vanished']


def unusable_cache(case, tmp_path, monkeypatch, cache_root):
    """The cache root (or its parents) arranged as `case` names -> what the refusal must say of it."""
    base = cache_root.parent.parent                                       # $XDG_CACHE_HOME
    if case.startswith('parent-'):
        make(base, {'parent-group-writable': 0o775, 'parent-shared-like-tmp': 0o1777, 'parent-foreign': 0o755}[case])
        if case == 'parent-foreign':
            foreign(monkeypatch, base)
        return {'parent-group-writable': f'{base} is not', 'parent-shared-like-tmp': 'mode 1777', 'parent-foreign': f'uid {os.getuid() + 1}'}[case]
    if case == 'inside-a-repository':
        make(base)
        (base / '.git').mkdir()
        return f'{cache_root}: inside a git repository'
    if case == 'cannot-be-made':
        make(base.parent / 'ro', 0o500)                                   # $XDG_CACHE_HOME under a folder nothing can be made in
        monkeypatch.setenv('XDG_CACHE_HOME', str(base.parent / 'ro' / 'cache'))
        return 'Permission denied'
    make(base)
    make(base / 'dream')
    if case == 'root-a-link':
        (base / 'dream' / 'sleepwalk-runs').symlink_to(make(tmp_path / 'elsewhere'))
        return f'{cache_root}: not an absolute, link-free path'
    if case == 'root-a-file':
        cache_root.touch()
        return 'File exists'                                              # mkdir's word for it: nothing is made or changed
    make(cache_root)
    if case == 'root-not-0700':
        cache_root.chmod(0o755)
        return f"{cache_root}: not this user's with mode 0700 (uid {os.getuid()}, mode 0755)"
    if case == 'root-special-bit':
        cache_root.chmod(0o1700)
        return 'mode 1700'
    if case == 'root-foreign':
        foreign(monkeypatch, cache_root)
        return f'uid {os.getuid() + 1}, mode 0700'
    vanishing(monkeypatch, cache_root)
    return f'{cache_root}: no folder can be made there'


@pytest.mark.parametrize('case', CACHE_CASES)
async def test_a_cache_root_that_cannot_be_trusted_refuses_the_run_with_the_reason(tmp_path, monkeypatch, system_tmp, cache_root, case):
    reason = unusable_cache(case, tmp_path, monkeypatch, cache_root)
    seen = stub(monkeypatch)
    try:
        record = await run()
    finally:
        for folder in (tmp_path / 'ro', cache_root):
            if folder.is_dir():
                folder.chmod(0o700)
    refused(record, seen, system_tmp, '$XDG_RUNTIME_DIR is not set', reason)
    if case.startswith('parent-') or case == 'inside-a-repository':
        assert not cache_root.parent.exists()                             # no dream folder made in the one refused


# --- nothing is made where the run would then be refused (the DREAM-202 gate's limit b) ---------------------------

ABOVE_CASES = {'shared-like-tmp': (0o1777, 'mode 1777'), 'group-writable': (0o775, 'mode 0775'),
               'foreign': (0o755, f'uid {os.getuid() + 1}'), 'inside-a-repository': (0o755, 'inside a git repository')}


@pytest.mark.parametrize('present', [False, True], ids=['cache-home-missing', 'cache-home-present'])
@pytest.mark.parametrize('case', list(ABOVE_CASES))
async def test_a_cache_directory_under_a_refused_folder_makes_nothing_there(tmp_path, monkeypatch, system_tmp, case, present):
    """$XDG_CACHE_HOME under a folder the rules refuse -- /tmp/x, say: shared like /tmp; or group-writable, another
    user's, inside a repository -- whether it is missing or there already: the run is refused with the reason, and
    nothing was made in that folder first (no $XDG_CACHE_HOME, dream or sleepwalk-runs folder left behind)."""
    mode, reason = ABOVE_CASES[case]
    folder = make(tmp_path / 'refused', mode)                             # the owner's underneath: a mkdir in it succeeds
    if case == 'foreign':
        foreign(monkeypatch, folder)
    elif case == 'inside-a-repository':
        (folder / '.git').mkdir()
    cache_home = folder / 'x'
    if present:
        make(cache_home)
    monkeypatch.setenv('XDG_CACHE_HOME', str(cache_home))
    before = sorted(folder.rglob('*'))
    seen = stub(monkeypatch)
    refused(await run(), seen, system_tmp, f'{cache_home}/dream/sleepwalk-runs: ', reason)
    assert sorted(folder.rglob('*')) == before


async def test_a_link_above_the_cache_root_makes_nothing_where_it_leads(tmp_path, monkeypatch, system_tmp, cache_root):
    """The cache directory's dream folder a link into a shared folder: the run is refused (not link-free), and no
    sleepwalk-runs folder was made at the link's end first."""
    end = make(make(tmp_path / 'shared', 0o1777) / 'end')
    make(cache_root.parent.parent)
    cache_root.parent.symlink_to(end)
    seen = stub(monkeypatch)
    refused(await run(), seen, system_tmp, f'{cache_root}: not an absolute, link-free path')
    assert list(end.iterdir()) == []


@pytest.mark.parametrize('case', UNUSABLE_RUNTIME)
async def test_the_refusal_names_what_is_wrong_with_each_location(tmp_path, monkeypatch, system_tmp, cache_root, case):
    """Both roots unusable -- the runtime directory in each way there is, the cache root inside a repository: the
    record's error says so for each, in that order, and the run was never started."""
    root = unusable_runtime(case, tmp_path, monkeypatch)
    make(cache_root.parent.parent)
    (cache_root.parent.parent / '.git').mkdir()
    seen = stub(monkeypatch)
    try:
        record = await run()
    finally:
        if root is not None and root.is_dir():
            root.chmod(0o700)
    refused(record, seen, system_tmp, RUNTIME_REASON[case], f'{cache_root}: inside a git repository')
    assert record['error'].index(RUNTIME_REASON[case]) < record['error'].index(str(cache_root))
    assert '; ' in record['error']                                        # one reason per location


async def test_a_special_bit_on_the_runtime_directory_is_refused_by_the_exact_mode_rule(tmp_path, monkeypatch, system_tmp, cache_root):
    """Codex round 2, item 5: 01700 is not 0700 -- S_IMODE compares the special bits too, so a sticky runtime directory
    falls through to the cache root, and a sticky cache root is refused by name."""
    root = runtime(tmp_path, monkeypatch)
    root.chmod(0o1700)
    seen = stub(monkeypatch)
    ran_under(await run(), seen, cache_root, system_tmp)
    cache_root.chmod(0o1700)
    system_tmp[1].clear()
    seen = stub(monkeypatch)
    refused(await run(), seen, system_tmp, f'{root}: not this user', 'mode 1700', f'{cache_root}: not this user')
