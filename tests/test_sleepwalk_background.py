"""Sleepwalk phase 4 (DREAM-161): the background timer's unit files and toggle against a fake systemctl, and
`dream sleepwalk run-due` with a fake clock and a stubbed runner, sharing its lock with an open Dream."""
import asyncio
import subprocess
import sys
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import httpx
import pytest

from dream import config
from dream.core import council_config, moe
from dream.gui.bus import EventBus
from dream.gui.server import StudioServer
from dream.sleepwalk import background, runner, schedule, store
from dream.sleepwalk.scheduler import run_due

NY = ZoneInfo('America/New_York')
CHOICES = [{'key': k, 'label': k, 'available': True, 'efforts': [], 'models': []} for k in ('machx', 'codex', 'xai')]
FAKE = """#!/bin/sh
echo "$@" >> "$(dirname "$0")/calls.log"
state="$(dirname "$0")/state"
case "$2" in
  is-enabled) cat "$state" 2>/dev/null || echo disabled ;;
  enable) echo enabled > "$state" ;;
  disable) echo disabled > "$state" ;;
esac
exit 0
"""


@pytest.fixture
def fake_home(tmp_path, monkeypatch):
    bin_dir = tmp_path / 'bin'
    bin_dir.mkdir()
    (bin_dir / 'systemctl').write_text(FAKE)
    (bin_dir / 'systemctl').chmod(0o755)
    monkeypatch.setenv('PATH', f"{bin_dir}:/usr/bin:/bin")
    monkeypatch.setenv('HOME', str(tmp_path / 'home'))
    monkeypatch.setenv('XDG_CONFIG_HOME', str(tmp_path / 'home' / '.config'))
    monkeypatch.setattr(sys, 'executable', str(tmp_path / 'home' / 'dream' / '.venv' / 'bin' / 'python'))
    monkeypatch.setattr(config, 'ROOT', tmp_path / 'home' / 'dream')
    monkeypatch.delenv('DREAM_EXTENSION_SETTINGS', raising=False)      # the test harness points it outside HOME
    return bin_dir


def test_the_toggle_writes_enables_and_removes_the_user_timer(fake_home, tmp_path):
    units = tmp_path / 'home' / '.config' / 'systemd' / 'user'
    assert background.state() == 'off'
    assert background.turn_on() == 'on'
    service = (units / 'dream-sleepwalk.service').read_text()
    assert 'ExecStart="%h/dream/.venv/bin/python" -m dream sleepwalk run-due' in service
    assert 'WorkingDirectory=%h/dream\n' in service and 'Type=oneshot' in service
    timer = (units / 'dream-sleepwalk.timer').read_text()
    assert 'OnCalendar=*:0/15' in timer and 'WantedBy=timers.target' in timer
    assert str(tmp_path) not in timer and 'linger' not in (service + timer).lower()
    assert background.turn_off() == 'off' and list(units.iterdir()) == []
    calls = (fake_home / 'calls.log').read_text().splitlines()
    assert calls[1:3] == ['--user daemon-reload', '--user enable --now dream-sleepwalk.timer']
    assert '--user disable --now dream-sleepwalk.timer' in calls


def test_refuses_outside_home_and_without_systemctl(fake_home, tmp_path, monkeypatch):
    monkeypatch.setattr(config, 'ROOT', Path('/opt/elsewhere'))
    with pytest.raises(ValueError, match='home folder'):
        background.turn_on()
    assert not (tmp_path / 'home' / '.config').exists()
    monkeypatch.setattr(config, 'ROOT', tmp_path / 'home' / 'dream')
    monkeypatch.setenv('PATH', str(tmp_path / 'empty'))
    assert background.state() == 'unavailable'
    with pytest.raises(ValueError, match='systemctl was not found'):
        background.turn_on()


@pytest.fixture(autouse=True)
def logged_in(monkeypatch):
    monkeypatch.setattr(background, '_logind', lambda prop: {'State': 'active', 'Linger': 'no'}[prop])


@pytest.fixture
def home(tmp_path, monkeypatch):
    monkeypatch.setattr(store, 'ROOT', tmp_path / 'sleepwalk')
    monkeypatch.setattr(council_config, 'provider_choices', lambda **_: CHOICES)
    return tmp_path / 'sleepwalk'


def seed(runner_key='codex', last=datetime(2026, 9, 24, 7, 30, tzinfo=NY), **extra):
    auto = store.save({'title': 'Notes', 'icon': 'sun', 'instructions': 'Say hello.',
                       'triggers': [{'every': 'day', 'at': '07:30'}], 'runner': {'provider': runner_key}, **extra})
    data = store._read()[0]
    data['automations'][0]['last_slot'] = last.isoformat()
    store._write(store.ROOT / 'automations.json', data)
    return auto


@pytest.mark.parametrize('runner_key,fallback,status,used', [
    ('codex', None, 'OK', ['codex']),
    ('machx', None, 'SKIPPED', []),
    ('machx', {'provider': 'xai'}, 'OK', ['xai'])])
async def test_run_due_runs_cloud_runners_only(home, monkeypatch, capsys, runner_key, fallback, status, used):
    calls = []

    async def consult(provider, question, context='', **kw):
        calls.append(provider)
        return 'Hello.'

    async def never():
        raise AssertionError('the background run must not probe or start the local engine')
    monkeypatch.setattr(moe, 'consult_advisor', consult)
    monkeypatch.setattr(runner, '_local_loaded', never)
    seed(runner_key, fallback=fallback, missed='skip')
    assert await run_due(datetime(2026, 9, 25, 7, 46, tzinfo=NY)) == 0      # 16 min late: on time for the timer
    rows = store.runs()
    assert [r['status'] for r in rows] == [status] and calls == used
    if status == 'SKIPPED':
        assert 'cloud runners only' in store.read_run(rows[0]['automation'], rows[0]['run'])['error']
    assert '1 run(s)' in capsys.readouterr().out
    assert await run_due(datetime(2026, 9, 25, 7, 50, tzinfo=NY)) == 0 and len(store.runs()) == 1


async def test_run_due_yields_to_an_open_dream(home, monkeypatch, tmp_path, capsys):
    calls = []

    async def consult(*_a, **_k):
        calls.append(1)
        return 'Hello.'
    monkeypatch.setattr(moe, 'consult_advisor', consult)
    seed(last=schedule.now())                           # nothing due for the session at the real time
    due_at = (schedule.now() + timedelta(days=1)).replace(hour=7, minute=31, second=0)
    srv = StudioServer(EventBus(), on_prompt=lambda p: None, sleepwalk=True,
                       session={'workspace': str(tmp_path), 'model': 'fixture'})
    await srv.start()
    try:
        for _ in range(100):
            if srv._sleepwalk._lock is not None:
                break
            await asyncio.sleep(0.02)
        assert srv._sleepwalk._lock is not None
        assert await run_due(due_at) == 0
        assert 'an open Dream fires the runs' in capsys.readouterr().out and calls == []
    finally:
        await srv.stop()
    assert await run_due(due_at) == 0 and calls == [1]


def test_the_cli_entry_point(tmp_path):
    done = subprocess.run([sys.executable, '-m', 'dream', 'sleepwalk', 'run-due'], capture_output=True, text=True,
                          timeout=120, cwd=Path(__file__).resolve().parents[1])
    assert done.returncode == 0 and done.stdout.startswith('sleepwalk run-due: ')     # 0 runs, or no login session


async def test_the_route(fake_home, home, tmp_path):
    srv = StudioServer(EventBus(), on_prompt=lambda p: None, session={'workspace': str(tmp_path), 'model': 'fixture'})
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=srv.app), base_url='http://127.0.0.1',
                                 headers={'X-Dream-Token': srv.token}) as client:
        assert (await client.get('/api/sleepwalk')).json()['background'] == 'off'
        assert (await client.post('/api/sleepwalk/background', json={'on': True})).json() == {'background': 'on'}
        assert (await client.post('/api/sleepwalk/background', json={'on': 'yes'})).status_code == 400
        assert (await client.post('/api/sleepwalk/background', json={'on': False})).json() == {'background': 'off'}
