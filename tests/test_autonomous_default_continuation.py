"""A goal does not silently stop after twelve ordinary continuation turns."""
import pytest

from dream.core.loop import AutonomousLoop
from test_durable_autonomy import Worker


async def test_default_continues_past_twelve_until_worker_finishes(tmp_path):
    worker = Worker(tmp_path, ['STATUS: CONTINUE\nNEXT: Continue the next part'] * 13 + ['STATUS: DONE'])
    result = await AutonomousLoop(worker, state_dir=tmp_path / 'runs', evaluate=False).run(
        'Complete all parts', acceptance_criteria='All parts are complete')
    assert result.status == 'unverified'  # Disabling review cannot fabricate success.
    assert result.iterations == 14
    assert len(worker.prompts) == 14


async def test_explicit_limit_still_stops(tmp_path):
    worker = Worker(tmp_path, ['STATUS: CONTINUE\nNEXT: Continue the next part'] * 3)
    loop = AutonomousLoop(worker, max_iterations=2, state_dir=tmp_path / 'runs', evaluate=False)
    result = await loop.run('Complete all parts', acceptance_criteria='All parts are complete')
    assert result.status == 'budget' and result.iterations == 2
    assert len(worker.prompts) == 2


@pytest.mark.parametrize('limit', [0, -1, True, 2.5, '4'])
def test_invalid_explicit_limits_are_rejected(tmp_path, limit):
    with pytest.raises(ValueError, match='positive integer'):
        AutonomousLoop(Worker(tmp_path), max_iterations=limit)


@pytest.mark.parametrize('arguments,expected', [([], None), (['--iterations', '3'], 3)])
def test_cli_passes_only_requested_iteration_limit(monkeypatch, tmp_path, arguments, expected):
    import dream.__main__ as entry
    seen = []
    class FakeApp:
        def __init__(self, **kwargs):
            self.mode = 'accept-edits'
        async def run_autonomous(self, goal, iterations):
            seen.append(iterations)
    monkeypatch.setattr('dream.tui.app.App', FakeApp)
    monkeypatch.setattr('sys.argv', ['dream', '--loop', 'Complete task', '--workspace', str(tmp_path), *arguments])
    entry.main()
    assert seen == [expected]


def test_cli_rejects_invalid_limit_before_app_start(monkeypatch):
    import dream.__main__ as entry
    def forbidden(**kwargs):
        pytest.fail('Invalid limit must be rejected before starting an app')
    monkeypatch.setattr('dream.tui.app.App', forbidden)
    monkeypatch.setattr('sys.argv', ['dream', '--loop', 'Complete task', '--iterations', '0'])
    with pytest.raises(SystemExit) as error:
        entry.main()
    assert error.value.code == 2
