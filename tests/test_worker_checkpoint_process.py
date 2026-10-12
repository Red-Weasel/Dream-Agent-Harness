"""Abrupt process exits release ownership without erasing durable uncertainty."""
import json
import subprocess
import sys
import time

import pytest

from dream.core.worker_checkpoints import WorkerCheckpointError, WorkerJournal


SESSION = 'checkpoint-process-fixture'
RUN = 'd' * 32
BINDING = {'parent': 'original-parent', 'contract': 'original-contract'}
CHILD = r'''
import os, sys, time
from dream.core.worker_checkpoints import WorkerJournal
root, mode = sys.argv[1:]
j = WorkerJournal(root, 'checkpoint-process-fixture', 'd' * 32, create=True)
j.__enter__()
j.create({'parent':'original-parent','contract':'original-contract'},
         {'messages':['original rules','task']},
         deadline_ns=time.time_ns()+60_000_000_000,max_rounds=3)
j.request_started(now_ns=time.time_ns())
if mode in ('tool', 'outcome', 'settled', 'publication'):
    j.tool_started('original-action')
if mode in ('outcome', 'settled', 'publication'):
    j.tool_finished('original-action', 'f'*64)
if mode == 'publication':
    # Die after the snapshot's immutable publication but before any checkpoint
    # acknowledgement can settle the existing request.
    save = j.store.save
    def crash(*args, **kwargs):
        save(*args, **kwargs)
        os._exit(23)
    j.store.save = crash
if mode in ('settled', 'publication'):
    j.settle({'messages':['original rules','task','checked observation'],
              'guards':{'completed_effects':['original-action']}})
os._exit(23)
'''


@pytest.mark.parametrize('mode', ['request', 'tool', 'outcome', 'settled', 'publication'])
def test_abrupt_driver_exit_preserves_debt_and_refuses_unsettled_restart(tmp_path, mode):
    result = subprocess.run([sys.executable, '-c', CHILD, str(tmp_path), mode],
                            capture_output=True, timeout=15)
    assert result.returncode == 23, result.stderr.decode()
    with WorkerJournal(tmp_path, SESSION, RUN) as journal:
        state = journal.inspect()
        assert state['requests'] == 1 and state['max_rounds'] == 3
        assert state['binding'] == BINDING
        if mode == 'settled':
            restored = journal.resume(BINDING, now_ns=time.time_ns())
            assert restored['pending'] is None
            assert restored['snapshot']['messages'][-1] == 'checked observation'
            assert restored['snapshot']['guards']['completed_effects'] == ['original-action']
        else:
            with pytest.raises(WorkerCheckpointError) as caught:
                journal.resume(BINDING, now_ns=time.time_ns())
            assert caught.value.code == 'unsettled'
            assert state['snapshot']['messages'] == ['original rules', 'task']
            if mode in ('outcome', 'publication'):
                assert state['pending']['tools']['original-action']['state'] == 'returned'


def test_completion_delivery_after_process_exit_acknowledges_without_execution(tmp_path):
    child = CHILD.split('j.request_started(')[0] + '''
j.finish('completed', {'result':'checked worker result','output_baseline':'original'})
os._exit(23)
'''
    result = subprocess.run([sys.executable, '-c', child, str(tmp_path), 'completed'],
                            capture_output=True, timeout=15)
    assert result.returncode == 23, result.stderr.decode()
    with WorkerJournal(tmp_path, SESSION, RUN) as journal:
        state = journal.inspect()
        assert state['snapshot']['result'] == 'checked worker result'
        assert state['requests'] == 0 and not state['acknowledged']
        with pytest.raises(WorkerCheckpointError) as caught:
            journal.request_started(now_ns=time.time_ns())
        assert caught.value.code == 'terminal'
        assert journal.acknowledge(state['completion_seq']) == state['completion_seq']
    with WorkerJournal(tmp_path, SESSION, RUN) as journal:
        assert journal.inspect()['acknowledged']
