"""Sleepwalk phase 2 (DREAM-158): slots in local time with DST, the tick, the scheduler lock across processes,
the missed-run policy and the local-model policy. A fake clock and a stubbed runner; no model, no network."""
import asyncio
import json
import subprocess
import sys
import textwrap
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from dream.core import council_config, moe
from dream.sleepwalk import runner, schedule, store
from dream.sleepwalk.scheduler import Scheduler

NY = ZoneInfo('America/New_York')
CHOICES = [{'key': k, 'label': k, 'available': True, 'efforts': [], 'models': []} for k in ('machx', 'codex', 'xai')]


def at(*args):
    return datetime(*args, tzinfo=NY)


def day(hhmm='07:30', **extra):
    return {'every': 'day', 'at': hhmm, **extra}


def test_daily_weekly_weekdays_and_hours():
    fri = at(2026, 9, 25, 12, 0)                      # a Friday
    assert schedule.latest_slot(day(), fri) == at(2026, 9, 25, 7, 30)
    assert schedule.next_slot(day(), fri) == at(2026, 9, 26, 7, 30)
    weekdays = {'every': 'weekdays', 'at': '08:00'}
    assert schedule.next_slot(weekdays, fri) == at(2026, 9, 28, 8, 0)     # Saturday and Sunday are skipped
    assert schedule.latest_slot(weekdays, at(2026, 9, 27, 12, 0)) == at(2026, 9, 25, 8, 0)
    weekly = {'every': 'week', 'at': '09:00', 'days': [0, 3]}             # Mondays and Thursdays
    assert schedule.latest_slot(weekly, fri) == at(2026, 9, 24, 9, 0)
    assert schedule.next_slot(weekly, fri) == at(2026, 9, 28, 9, 0)
    hours = {'every': 'hours', 'at': '07:00', 'n': 4}
    assert schedule.latest_slot(hours, fri) == at(2026, 9, 25, 11, 0)
    assert schedule.next_slot(hours, at(2026, 9, 25, 23, 30)) == at(2026, 9, 26, 3, 0)   # the series runs on


def test_day_31_falls_on_the_last_day_of_short_months():
    monthly = {'every': 'month', 'at': '06:00', 'day': 31}
    assert schedule.next_slot(monthly, at(2026, 9, 1, 0, 0)) == at(2026, 9, 30, 6, 0)
    assert schedule.next_slot(monthly, at(2026, 2, 1, 0, 0)) == at(2026, 2, 28, 6, 0)
    assert schedule.next_slot(monthly, at(2026, 10, 1, 0, 0)) == at(2026, 10, 31, 6, 0)


def test_dst_spring_forward_and_fall_back():
    utc = lambda d: d.astimezone(ZoneInfo('UTC'))
    # 2026-03-08: 02:30 does not exist in New York; the slot is the instant 03:30 EDT, once.
    spring = schedule.next_slot(day('02:30'), at(2026, 3, 8, 0, 0))
    assert utc(spring) == datetime(2026, 3, 8, 7, 30, tzinfo=ZoneInfo('UTC'))
    assert schedule.next_slot(day('02:30'), spring) == at(2026, 3, 9, 2, 30)
    # 2026-11-01: 01:30 happens twice; the slot is the first (EDT) and the second one does not fire again.
    first = schedule.next_slot(day('01:30'), at(2026, 11, 1, 0, 0))
    assert utc(first) == datetime(2026, 11, 1, 5, 30, tzinfo=ZoneInfo('UTC'))
    second_pass = datetime(2026, 11, 1, 6, 45, tzinfo=ZoneInfo('UTC')).astimezone(NY)      # 01:45 EST
    assert schedule.latest_slot(day('01:30'), second_pass) == first
    assert schedule.next_slot(day('01:30'), second_pass) == at(2026, 11, 2, 1, 30)
    # an ordinary day across the change is 25 hours long in real time and still fires once
    auto = {'triggers': [day('07:00')], 'last_slot': at(2026, 10, 31, 7, 0).isoformat()}
    assert schedule.due(auto, at(2026, 11, 1, 7, 1))[0] == at(2026, 11, 1, 7, 0)


def test_multi_trigger_and_one_catch_up_after_days_away():
    auto = {'triggers': [day('08:00', label='lesson'), day('21:00', label='quiz')],
            'last_slot': at(2026, 9, 20, 21, 0).isoformat()}
    slot, trigger, missed = schedule.due(auto, at(2026, 9, 21, 8, 2))
    assert slot == at(2026, 9, 21, 8, 0) and trigger['label'] == 'lesson' and not missed
    slot, trigger, missed = schedule.due(auto, at(2026, 9, 25, 22, 0))     # away four days: one newest slot
    assert slot == at(2026, 9, 25, 21, 0) and trigger['label'] == 'quiz' and missed
    auto['last_slot'] = slot.isoformat()
    assert schedule.due(auto, at(2026, 9, 25, 22, 0)) is None
    assert schedule.due({'triggers': [day()], 'created': '2026-09-25T08:00:00'}, at(2026, 9, 25, 9, 0)) is None


@pytest.fixture
def home(tmp_path, monkeypatch):
    monkeypatch.setattr(store, 'ROOT', tmp_path / 'sleepwalk')
    monkeypatch.setattr(council_config, 'provider_choices', lambda **_: CHOICES)
    return tmp_path / 'sleepwalk'


def seed(auto_overrides=None, last_slot=None):
    auto = store.save({'title': 'Notes', 'icon': 'sun', 'instructions': 'Say hello.', 'triggers': [day()],
                       'runner': {'provider': 'codex'}, **(auto_overrides or {})})
    data = json.loads((store.ROOT / 'automations.json').read_text())
    data['automations'][0]['last_slot'] = last_slot
    (store.ROOT / 'automations.json').write_text(json.dumps(data))
    return auto


async def test_tick_fires_once_and_the_missed_policy(home, monkeypatch):
    calls = []

    async def consult(provider, question, context='', **kw):
        calls.append(question)
        return 'Hello.'
    monkeypatch.setattr(moe, 'consult_advisor', consult)
    seed(last_slot=at(2026, 9, 24, 7, 30).isoformat())
    sched = Scheduler()
    try:
        await asyncio.gather(*await sched.tick(at(2026, 9, 25, 7, 31)))
        await asyncio.gather(*await sched.tick(at(2026, 9, 25, 7, 31, 30)))      # the next tick: nothing new
        assert len(calls) == 1 and 'Scheduled Fri 07:30' in calls[0]
        assert [r['status'] for r in store.runs()] == ['OK']
        # away three days, missed policy "skip": one SKIPPED row, no run
        auto = store.load()[0]
        store.save({**auto, 'missed': 'skip'})
        data = json.loads((home / 'automations.json').read_text())
        data['automations'][0]['last_slot'] = at(2026, 9, 25, 7, 30).isoformat()
        (home / 'automations.json').write_text(json.dumps(data))
        await asyncio.gather(*await sched.tick(at(2026, 9, 28, 12, 0)))
        assert len(calls) == 1 and [r['status'] for r in store.runs()] == ['SKIPPED', 'OK']
        assert 'Missed while Dream was closed' in store.read_run(auto['id'], store.runs()[0]['run'])['error']
        # disabled: nothing fires
        store.save({**store.load()[0], 'enabled': False})
        assert await sched.tick(at(2026, 9, 30, 7, 31)) == []
    finally:
        await sched.stop()


async def test_run_once_policy_catches_up_once(home, monkeypatch):
    calls = []

    async def consult(provider, question, context='', **kw):
        calls.append(question)
        return 'Hello.'
    monkeypatch.setattr(moe, 'consult_advisor', consult)
    seed(last_slot=at(2026, 9, 20, 7, 30).isoformat())
    sched = Scheduler()
    try:
        for minute in range(0, 4):
            await asyncio.gather(*await sched.tick(at(2026, 9, 25, 12, minute)))
        assert len(calls) == 1 and 'missed while Dream was closed' in calls[0]
    finally:
        await sched.stop()


async def test_no_store_means_no_lock_and_no_files(home):
    sched = Scheduler()
    assert await sched.tick(at(2026, 9, 25, 7, 31)) == [] and not home.exists()


CHILD = textwrap.dedent('''
    import asyncio, sys
    from datetime import datetime
    from pathlib import Path
    from zoneinfo import ZoneInfo
    from dream.core import moe
    from dream.sleepwalk import runner, store
    from dream.sleepwalk.scheduler import Scheduler
    store.ROOT = Path(sys.argv[1])
    log = Path(sys.argv[2])
    root = store.ROOT.parent / 'private-run-root'     # the run root conftest gives a test, here for a child process
    root.mkdir(mode=0o700, exist_ok=True)
    runner._ROOTS = (lambda: (root, ''),)
    async def consult(*a, **k):
        with log.open('a') as out:
            out.write('run\\n')
        await asyncio.sleep(0.5)
        return 'Hello.'
    moe.consult_advisor = consult
    async def main():
        sched = Scheduler()
        await asyncio.gather(*await sched.tick(datetime(2026, 9, 25, 7, 31, tzinfo=ZoneInfo('America/New_York'))))
        await sched.stop()
    asyncio.run(main())
''')


def test_two_processes_fire_once_in_total(home, tmp_path):
    seed(last_slot=at(2026, 9, 24, 7, 30).isoformat())
    log = tmp_path / 'calls.log'
    env_path = str(Path(__file__).resolve().parents[1])
    procs = [subprocess.Popen([sys.executable, '-c', CHILD, str(home), str(log)], cwd=env_path) for _ in range(2)]
    assert [p.wait(timeout=60) for p in procs] == [0, 0]
    assert log.read_text().splitlines() == ['run']
    assert [r['status'] for r in store.runs()] == ['OK']


@pytest.mark.parametrize('fallback,status,provider', [(None, 'SKIPPED', None), ({'provider': 'xai'}, 'OK', 'xai')])
async def test_local_runner_needs_a_loaded_model(home, monkeypatch, fallback, status, provider):
    used = []

    async def consult(provider, question, context='', **kw):
        used.append(provider)
        return 'Hello.'
    monkeypatch.setattr(moe, 'consult_advisor', consult)
    monkeypatch.setattr(runner, '_local_loaded', lambda: asyncio.sleep(0, result=False))
    auto = store.save({'title': 'Local', 'icon': 'sun', 'instructions': 'Say hello.', 'triggers': [day()],
                       'runner': {'provider': 'machx'}, 'fallback': fallback})
    record = await runner.run(auto)
    assert record['status'] == status and used == ([provider] if provider else [])
    if status == 'SKIPPED':
        assert 'local model was not loaded' in record['error'] and store.runs()[0]['status'] == 'SKIPPED'
    monkeypatch.setattr(runner, '_local_loaded', lambda: asyncio.sleep(0, result=True))
    used.clear()
    assert (await runner.run(auto))['status'] == 'OK' and used == ['machx']


def test_fallback_and_missed_are_validated(home):
    base = {'title': 'x', 'icon': 'sun', 'instructions': 'y', 'triggers': [], 'runner': {'provider': 'machx'}}
    for bad in ({'missed': 'later'}, {'fallback': {'provider': 'machx'}}, {'fallback': {'provider': 'grok'}}):
        with pytest.raises(ValueError):
            store.save({**base, **bad})
    saved = store.save(base)
    assert saved['missed'] == 'run_once' and saved['fallback'] is None and saved['last_slot']


def test_every_n_hours_runs_on_across_midnight():
    five = {'every': 'hours', 'at': '00:00', 'n': 5, 'from': '2026-09-25'}
    assert schedule.next_slot(five, at(2026, 9, 25, 20, 30)) == at(2026, 9, 26, 1, 0)
    assert schedule.latest_slot(five, at(2026, 9, 26, 0, 30)) == at(2026, 9, 25, 20, 0)
    late = {'every': 'hours', 'at': '22:00', 'n': 4}
    assert schedule.next_slot(late, at(2026, 9, 25, 22, 30)) == at(2026, 9, 26, 2, 0)
    fired, moment = [], at(2026, 9, 25, 0, 0)
    while moment < at(2026, 9, 27, 0, 0):
        moment = schedule.next_slot(five, moment)
        fired.append(moment)
    assert all(b - a == timedelta(hours=5) for a, b in zip(fired, fired[1:]))


def test_a_last_slot_in_the_future_does_not_silence_the_schedule():
    auto = {'triggers': [day()], 'last_slot': at(2027, 9, 25, 7, 30).isoformat()}   # claimed while the clock ran ahead
    assert schedule.due(auto, at(2026, 9, 26, 7, 45)) is None                      # clamped: nothing is replayed
    assert schedule.due(auto, at(2026, 9, 27, 7, 31))[:3:2] == (at(2026, 9, 27, 7, 30), False)   # the next slot fires
    auto['last_slot'] = at(2026, 10, 2, 7, 0).isoformat()                          # under a week ahead: honoured
    assert schedule.due(auto, at(2026, 9, 26, 7, 31)) is None


@pytest.mark.parametrize('back', [timedelta(minutes=2), timedelta(hours=3), timedelta(days=2)])
def test_clock_stepped_back_never_refires(back):
    auto = {'triggers': [day()], 'last_slot': at(2026, 9, 25, 7, 30).isoformat()}   # fired Friday 07:30
    moment, fired = at(2026, 9, 25, 7, 31) - back, []
    while moment < at(2026, 9, 25, 7, 45):
        if (found := schedule.due(auto, moment)) is not None:
            fired.append(found[0])
            auto['last_slot'] = found[0].isoformat()
        moment += timedelta(seconds=30)
    assert fired == []


@pytest.mark.parametrize('prefix', [':', ''])
def test_tz_may_name_a_zone_file(monkeypatch, prefix):
    path = Path('/usr/share/zoneinfo/Asia/Kolkata')
    if not path.is_file():
        pytest.skip('no system zone files')
    monkeypatch.setenv('TZ', prefix + str(path))
    assert schedule.now().utcoffset() == timedelta(hours=5, minutes=30)


async def test_a_run_cut_off_by_closing_dream_is_recorded_failed_once(home, monkeypatch):
    started = asyncio.Event()

    async def hang(*_a, **_k):
        started.set()
        await asyncio.Event().wait()
    monkeypatch.setattr(moe, 'consult_advisor', hang)
    auto = seed(last_slot=at(2026, 9, 24, 7, 30).isoformat())
    first = Scheduler()
    await first.tick(at(2026, 9, 25, 7, 31))
    try:
        await asyncio.wait_for(started.wait(), timeout=5)
    except TimeoutError:                                   # a refused run never reaches the runner: fail, don't hang
        await first.stop()
        pytest.fail('the run did not reach the runner in 5 s: refused before consult_advisor? its record(s): '
                    f'{[(r["status"], r.get("error")) for r in store.runs()]}')
    await first.stop()                                     # Dream closes mid-run
    assert store.runs() == [] and store.load()[0]['pending'] == ['Scheduled Fri 07:30']
    second = Scheduler()
    try:
        assert await second.tick(at(2026, 9, 25, 7, 45)) == []     # not retried
        await second.tick(at(2026, 9, 25, 7, 46))
    finally:
        await second.stop()
    rows = store.runs()
    assert [r['status'] for r in rows] == ['FAILED'] and store.load()[0]['pending'] == []
    assert 'Interrupted' in store.read_run(auto['id'], rows[0]['run'])['error']


async def test_only_an_already_running_error_is_recorded_as_skipped(home, monkeypatch):
    auto = seed(last_slot=at(2026, 9, 24, 7, 30).isoformat())

    async def refuse(*_a, **_k):
        raise store.AlreadyRunning('This automation is already running')
    monkeypatch.setattr(runner, 'run', refuse)
    await Scheduler()._run(auto, 'Scheduled')
    assert [r['status'] for r in store.runs()] == ['SKIPPED']

    async def broken(*_a, **_k):
        raise ValueError('a damaged store')
    monkeypatch.setattr(runner, 'run', broken)
    await Scheduler()._run(auto, 'Scheduled')                              # recorded with its real reason
    assert [r['status'] for r in store.runs()] == ['FAILED', 'SKIPPED']
    assert 'a damaged store' in store.read_run(auto['id'], store.runs()[0]['run'])['error']


@pytest.mark.parametrize('status,body,serving,loaded', [
    (200, {'data': [{'id': 'm'}]}, True, True),                                     # ie serve: listens once loaded
    (200, {'data': [{'id': 'a', 'status': 'loading'}]}, True, False),                # a supervised layout, loading
    (200, {'data': [{'id': 'a', 'status': 'loading'}, {'id': 'b', 'status': 'ready'}]}, True, True),
    (404, {'error': 'not here'}, False, False),                                      # another process on the port
    (200, ['not', 'a', 'list'], True, False),
])
def test_local_model_checks(monkeypatch, status, body, serving, loaded):
    import httpx
    from dream.local import machx
    monkeypatch.setattr(machx.httpx, 'get', lambda url, timeout: httpx.Response(status, json=body))
    assert machx.is_serving() is serving and machx.model_loaded() is loaded
