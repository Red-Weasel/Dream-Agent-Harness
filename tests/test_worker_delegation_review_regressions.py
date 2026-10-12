"""Reproduce independently reviewed parent ownership admission gaps."""
import asyncio
from copy import deepcopy
import time

import pytest

from test_worker_delegations import parent, reserve, child, terminal, reopen, EXECUTION, DENIED


@pytest.mark.parametrize('deliveries', [1, 2])
async def test_revoked_child_never_regains_parent_authority_after_terminal_delivery(parent, deliveries):
    binding = reserve(parent)
    ready = asyncio.Event()
    async def escaped():
        await ready.wait()
        assert not parent.c.actions_admitted
        with pytest.raises(PermissionError):
            parent.c.begin_action('write_file', {})
    with parent.c.claim_child(binding, execution_binding=EXECUTION):
        task = asyncio.create_task(escaped())
    for number in range(deliveries):
        if number:
            binding = reserve(parent, worker_id='b' * 32)
            with parent.c.claim_child(binding, execution_binding=EXECUTION):
                assert parent.c.actions_admitted
        with child(parent, binding) as journal:
            terminal(journal, binding)
            parent.c.record_child_return(binding, journal)
    assert parent.c.actions_admitted  # The real parent remains admitted.
    ready.set()
    await asyncio.wait_for(task, 2)
    assert parent.c.unresolved_actions == {}


async def test_revoked_child_cannot_claim_a_later_delegation(parent):
    original = reserve(parent)
    ready = asyncio.Event()
    next_binding = None
    async def escaped():
        await ready.wait()
        with pytest.raises(PermissionError):
            with parent.c.claim_child(next_binding, execution_binding=EXECUTION):
                pytest.fail('Revoked child obtained a later claim')
    with parent.c.claim_child(original, execution_binding=EXECUTION):
        task = asyncio.create_task(escaped())
    with child(parent, original) as journal:
        terminal(journal, original)
        parent.c.record_child_return(original, journal)
    next_binding = reserve(parent, worker_id='b' * 32)
    ready.set()
    await asyncio.wait_for(task, 2)


@pytest.mark.parametrize('status', ['budget', 'error', 'need_input'])
def test_persisted_closed_parent_cannot_claim_execution(parent, status):
    binding = reserve(parent)
    parent.c.journal.record('parent_closed', status=status)
    c = reopen(parent)
    with pytest.raises(DENIED):
        with c.claim_child(binding, execution_binding=EXECUTION):
            pytest.fail('Closed parent admitted child')
    assert binding['delegation_id'] in c.pending_children


@pytest.mark.parametrize('field,value', [('version', True), ('version', 1.0), ('revision', True)])
def test_worker_binding_requires_type_exact_original(parent, field, value):
    binding = reserve(parent)
    changed = deepcopy(binding)
    changed[field] = value
    with child(parent, binding) as journal:
        journal.create(changed, {}, deadline_ns=time.time_ns() + 60_000_000_000, max_rounds=3)
        journal.finish('completed', {'parent_result': {'text': 'result', 'is_error': False}})
        revision = parent.c._state['action_revision']
        with pytest.raises(DENIED):
            parent.c.record_child_return(binding, journal)
        assert parent.c._state['action_revision'] == revision
        assert binding['delegation_id'] in parent.c.pending_children
        assert not journal.inspect()['acknowledged']
