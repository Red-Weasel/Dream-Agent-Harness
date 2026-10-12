"""A Dream terminal opened from DeepSleep starts in auto: nobody sits at a swarm's panes to answer each write.

The owner, 2026-10-09, after one prompt kept a session waiting 98 minutes (runtime log: approval_wait_s 5905 of 5955):
"do whatever needs to be to stop making me approve everything". Every Dream terminal was launched with --mode ask,
in which each workspace write and each shell command asks.
"""
from dream.gui import deepsleep
from dream.gui.deepsleep import DeepSleep


async def test_dream_terminal_starts_in_auto_mode(tmp_path, monkeypatch):
    seen = []

    def capture(argv):
        seen.append(list(argv))
        raise RuntimeError('fixture: stop before anything is spawned')

    monkeypatch.setattr(deepsleep, 'login_supervised_command', capture)
    board = DeepSleep(lambda: tmp_path, lambda: 'http://127.0.0.1:1')
    node = await board.create({'provider': 'codex'})
    try:
        assert node.profile == 'dream' and seen, 'the Dream terminal launch was not reached'
        argv = seen[0]
        assert argv[argv.index('--mode') + 1] == 'auto'
        assert node.view()['initial_permissions'] == 'Auto'
    finally:
        await board.stop()


def test_native_terminal_reports_its_cli_permissions():
    node = deepsleep.Node('fixture', 'codex', '', 'fixture', {}, profile='native')
    assert node.view()['initial_permissions'] == 'Native CLI settings'
