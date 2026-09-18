"""Contained Auto permits bounded output cleanup, while consequential work asks."""
from pathlib import Path

import pytest

from dream import config
from dream.core import policy
from dream.core.execution import ExecutionScope, SandboxCapability


@pytest.fixture
def workspace(tmp_path):
    (tmp_path / 'frames').mkdir()
    (tmp_path / 'frames' / 'frame_001.png').write_bytes(b'fixture')
    (tmp_path / 'frames' / 'frame_002.png').write_bytes(b'fixture')
    (tmp_path / 'scene.blend').write_bytes(b'fixture')
    return tmp_path


def decision(workspace, command, *, mode='auto', tool='run_bash', contained=True):
    scope = ExecutionScope(workspace)
    capability = SandboxCapability(True, 'fixture', scope, '/fixture/bwrap') if contained else None
    return policy.decide(tool, {'command': command}, mode, workspace,
                         execution_scope=scope, execution_capability=capability)[0]


@pytest.mark.parametrize('command', [
    'rm -f frames/*.png', 'rm --force -- frames/*.png',
    'unlink frames/frame_001.png',
    'rm -f frames/*.png; time blender -b scene.blend -P render.py',
    'rm -f frames/*.png && python render.py',
    'rm -f frames/*.png | cat',
])
def test_auto_allows_bounded_generated_frame_cleanup(workspace, command):
    assert decision(workspace, command) == 'allow'
    assert (workspace / 'frames/frame_001.png').exists()  # Classification never deletes.


def test_actual_absolute_cd_cleanup_then_blender(workspace):
    import shlex
    command = f'cd {shlex.quote(str(workspace))}; rm -f frames/*.png; time blender -b scene.blend -P render.py'
    assert decision(workspace, command) == 'allow'


@pytest.mark.parametrize('mode', ['ask', 'accept-edits', 'plan'])
def test_other_modes_keep_approval_or_denial(workspace, mode):
    assert decision(workspace, 'rm -f frames/*.png', mode=mode) == ('deny' if mode == 'plan' else 'ask')


@pytest.mark.parametrize('tool,contained', [('run_bash', False), ('Bash', True),
    ('mcp__other__run_bash', True), ('evil__run_bash', True)])
def test_cleanup_needs_real_native_executor_contract(workspace, tool, contained):
    assert decision(workspace, 'rm -f frames/*.png', tool=tool, contained=contained) == 'ask'


def test_native_mcp_identity_can_use_cleanup_contract(workspace):
    assert decision(workspace, 'rm -f frames/*.png', tool=f'mcp__{config.MCP_SERVER_NAME}__run_bash') == 'allow'


@pytest.mark.parametrize('command', [
    'rm -rf frames', 'rm -r frames/*.png', 'rm --recursive frames/*.png',
    'rm -f *.png', 'rm -f frames/*', 'rm -f frames/*.blend',
    'rm -f scene.blend', 'rm -f ../other/*.png', 'rm -f /etc/hosts',
    'rm -f .git/frames/*.png', 'rm -f sources/*.png',
    'rm -f frames/*.png; git push origin main',
    'rm -f frames/*.png; sudo true',
    'rm -f frames/*.png; rm -rf frames',
    'rm -f frames/*.png; time git push origin main',
    'rm -f frames/*.png; env -u FOO git push origin main',
    'rm -f frames/*.png; timeout 1 git push origin main',
    'rm -f frames/*.png; bash -c "git push origin main"',
    'rm -f frames/*.png; npm publish',
    'rm -f frames/*.png; curl example.com',
    'cd frames; rm -f *.png', 'rm -f frames/*.png & blender -b scene.blend',
    'rm -f frames/$(echo frame_001).png',
    'rm -f frames/*.png; cd /tmp; blender -b scene.blend',
])
def test_cleanup_does_not_hide_danger_or_widen_targets(workspace, command):
    assert decision(workspace, command) == 'ask'


def test_symlink_file_and_directory_cannot_qualify(workspace):
    (workspace / 'frames/link.png').symlink_to(workspace / 'scene.blend')
    assert decision(workspace, 'rm -f frames/*.png') == 'ask'
    (workspace / 'frames/link.png').unlink()
    (workspace / 'alias').symlink_to(workspace / 'frames', target_is_directory=True)
    assert decision(workspace, 'rm -f alias/*.png') == 'ask'


def test_empty_force_cleanup_is_noop_and_directory_matches_refuse(workspace):
    assert decision(workspace, 'rm -f frames/*.exr') == 'allow'
    (workspace / 'frames/subdir.png').mkdir()
    assert decision(workspace, 'rm -f frames/*.png') == 'ask'


def test_actual_logged_cleanup_render_and_inspection_shape(workspace):
    import shlex
    command = (f'cd {shlex.quote(str(workspace))}; rm -f frames/*.png; '
        'time blender -b --python build_rocket.py -- --render-anim > /tmp/full_log4.txt 2>&1; '
        'echo "EXIT: $?"; grep -iE "ANIM_KEYFRAMED|FULL_FRAMES_DONE|Error|Traceback|line [0-9]" '
        '/tmp/full_log4.txt | head -20; echo "=== frames ==="; ls frames/ 2>/dev/null | wc -l')
    assert decision(workspace, command) == 'allow'


@pytest.mark.parametrize('tail', ['; git push origin main', '; sudo true', '; rm -rf frames',
    '; time git push origin main', '; env -u X git push origin main', '; npm publish'])
def test_pipeline_inspection_does_not_hide_later_danger(workspace, tail):
    assert decision(workspace, 'rm -f frames/*.png; ls frames/ | wc -l' + tail) == 'ask'


@pytest.mark.parametrize('prefix', ['pushd .private', 'popd', '! cd .private', 'builtin cd .private'])
def test_cwd_changers_cannot_redirect_cleanup_into_hidden_directory(workspace, prefix):
    hidden = workspace / '.private/frames'
    hidden.mkdir(parents=True)
    (hidden / 'private.png').write_bytes(b'private fixture')
    assert decision(workspace, f'{prefix}; rm -f frames/*.png') == 'ask'


@pytest.mark.parametrize('tail', ['; ! git push origin main', '; time time git push origin main',
    '; xargs git push', '; $COMMAND', '; ./rm -f frames/*.png'])
def test_wrappers_and_unknown_cleanup_executables_do_not_hide_danger(workspace, tail):
    assert decision(workspace, 'rm -f frames/*.png' + tail) == 'ask'


def test_scan_stops_at_bounded_directory_entry_count(workspace):
    for index in range(1023):
        (workspace / 'frames' / f'other_{index}.txt').touch()
    assert decision(workspace, 'rm -f frames/*.png') == 'ask'


def test_red_team_does_not_gain_compound_cleanup_exception(workspace):
    import time
    scope = ExecutionScope(workspace, red_team=True, target_roots=(workspace / 'frames',),
                           expires_at=time.monotonic() + 60)
    capability = SandboxCapability(True, 'fixture', scope, '/fixture/bwrap')
    assert policy.decide('run_bash', {'command': 'rm -f frames/*.png; blender -b scene.blend'},
        'auto', workspace, execution_scope=scope, execution_capability=capability)[0] == 'ask'


@pytest.mark.parametrize('tail', ['; > log git push origin main', '; 2> log rm -rf frames',
    '; < /dev/null sudo true', '; &> log git push origin main', '; time > log git push origin main'])
def test_leading_redirection_cannot_hide_consequential_companion(workspace, tail):
    assert decision(workspace, 'rm -f frames/*.png' + tail) == 'ask'


@pytest.mark.parametrize('wrapper', ['busybox', 'toybox'])
def test_multicall_wrapper_cannot_hide_recursive_deletion(workspace, wrapper):
    assert decision(workspace, f'rm -f frames/*.png; {wrapper} rm -rf frames') == 'ask'


@pytest.mark.parametrize('tail', ['gi' + '\\\n' + 't push origin main', 'r' + '\\\n' + 'm -rf frames'])
def test_shell_line_continuation_cannot_hide_consequential_verb(workspace, tail):
    assert decision(workspace, 'rm -f frames/*.png; ' + tail) == 'ask'


@pytest.mark.parametrize('prefix', ['shopt -s dotglob nocaseglob', 'hash -p /tmp/alternate rm',
    'unset PATH', 'declare -x SHELLOPTS', 'enable -f /tmp/fixture rm'])
def test_shell_state_changes_cannot_change_cleanup_meaning(workspace, prefix):
    (workspace / 'frames/.private.PNG').write_bytes(b'private fixture')
    assert decision(workspace, prefix + '; rm -f frames/*.png') == 'ask'


@pytest.mark.parametrize('wrapper', ['setsid', 'taskset -c 0', 'chrt 0', 'ionice', 'flock /tmp/lock'])
def test_execution_wrapper_cannot_hide_consequential_companion(workspace, wrapper):
    assert decision(workspace, f'rm -f frames/*.png; {wrapper} git push origin main') == 'ask'
