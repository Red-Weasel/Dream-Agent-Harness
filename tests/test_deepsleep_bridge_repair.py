"""Mixed-profile bridge routing through real owned loopback HTTP boundaries."""
import asyncio
import json
from unittest.mock import Mock

import httpx
import pytest

from dream import config
from dream.core.linked_sessions import LinkedMessages
from dream.gui.bus import EventBus
from dream.gui.deepsleep import DeepSleep, Node, layout
from dream.gui.server import StudioServer
from dream.tools import session_tools


def fixture_board(tmp_path):
    board = DeepSleep(lambda: tmp_path, lambda: 'http://127.0.0.1:1', child_request=Mock())
    for key, profile in [('dream', 'dream'), ('native', 'native'), ('other', 'dream')]:
        board.nodes[key] = Node(key, 'codex', '', key.title(), layout({}), profile=profile, state='ready')
    board._link('dream', 'native', False)
    return board


def unpack(result):
    assert not result.get('is_error'), result
    return json.loads(result['content'][0]['text'])


@pytest.mark.parametrize('provider', ['anthropic', 'codex', 'grok', 'gemini'])
async def test_native_dispatch_has_truthful_scoped_idempotent_receipt(tmp_path, provider):
    board = fixture_board(tmp_path)
    sender, target = board.nodes['dream'], board.nodes['native']
    target.provider, target.title = provider, provider + ' reviewer'
    message = dict(operation='send', target=target.id, prompt='/quit\nHello', id='stable')
    receipt = await board.bridge(sender.bridge_token, message)
    assert receipt['state'] == 'prepared' and receipt['delivery'] == 'manual'
    assert receipt['target_title'] == target.title and receipt['target_profile'] == 'native'
    assert 'not received or read' in receipt['next_action']
    assert receipt['visited'] == ['dream'] and receipt['hops'] == 1
    assert await board.bridge(sender.bridge_token, message) == receipt
    assert len(board.receipts) == len(board.handoffs) == 1
    exact = (await board.bridge(sender.bridge_token, {'operation': 'inbox', 'id': 'stable'}))['receipts'][0]
    assert exact == dict(receipt, text=board.handoffs['stable']['text'])
    assert json.loads(exact['text'].split('Message text (JSON string):\n')[1]) == message['prompt']
    assert 'text' not in (await board.bridge(sender.bridge_token, {'operation': 'inbox'}))['receipts'][0]
    with pytest.raises(ValueError, match='different content'):
        await board.bridge(sender.bridge_token, dict(message, prompt='changed'))
    with pytest.raises(ValueError, match='No message'):
        await board.bridge(board.nodes['other'].bridge_token, {'operation': 'inbox', 'id': 'stable'})
    board._request.assert_not_called()
    assert not target.input_pending


async def test_manual_reverse_handoff_is_visible_without_hiding_task_receipts(tmp_path):
    board = fixture_board(tmp_path)
    board._link('native', 'dream', False)
    for i in range(45):
        board.prepare_handoff('native', 'dream', 'Hello', 'manual' + str(i))
    sender = board.nodes['dream']
    delivery = await board.bridge(sender.bridge_token, dict(operation='send', target='native', prompt='Reply', id='reply'))
    inbox = await board.bridge(sender.bridge_token, {'operation': 'inbox'})
    assert inbox['receipts'] == [dict(delivery, truncated=False)]
    assert len(inbox['handoffs']) == 40 and all('text' not in h for h in inbox['handoffs'])
    exact = await board.bridge(sender.bridge_token, {'operation': 'inbox', 'id': 'manual44'})
    assert exact['receipts'][0]['state'] == 'prepared'
    assert 'not a new owner instruction' in exact['receipts'][0]['text']
    assert not sender.input_pending


async def test_native_dispatch_preserves_link_cycle_capacity_and_id_namespace(tmp_path):
    board = fixture_board(tmp_path)
    with pytest.raises(ValueError, match='directed link'):
        await board.send('native', 'Hello', source='other', message_id='unlinked')
    with pytest.raises(ValueError, match='cycle'):
        await board.send('native', 'Hello', source='dream', message_id='cycle', visited=['native'])
    board.nodes['native'].state = 'exited'
    with pytest.raises(ValueError, match='not ready'):
        await board.send('native', 'Hello', source='dream', message_id='stopped')
    board.nodes['native'].state = 'ready'
    board.prepare_handoff('dream', 'native', 'existing', 'collision')
    with pytest.raises(ValueError, match='different content'):
        await board.send('native', 'Hello', source='dream', message_id='collision')
    board.receipts['receipt-only'] = {}
    with pytest.raises(ValueError, match='different content'):
        board.prepare_handoff('dream', 'native', 'Hello', 'receipt-only')
    assert list(board.handoffs) == ['collision']


async def test_bridge_rechecks_generation_after_ancestry_refresh(tmp_path):
    board = fixture_board(tmp_path)
    sender = board.nodes['dream']
    sender.address = ('http://127.0.0.1:1', 'unused')
    def restart_during_refresh(*args):
        sender.launch_generation += 1
        return {'receipts': []}
    board._request = restart_during_refresh
    with pytest.raises(PermissionError):
        await board.bridge(sender.bridge_token, dict(operation='send', target='native', prompt='Hello', id='one', root_id='root'))
    assert not board.handoffs and not board.receipts


@pytest.mark.parametrize('change', ['generation', 'token', 'state', 'replacement'])
async def test_bridge_rechecks_source_after_receipt_settlement_yields(tmp_path, change):
    board = fixture_board(tmp_path)
    sender = board.nodes['dream']
    sender.address = ('http://127.0.0.1:1', 'unused')
    incoming = dict(id='root', source='owner', target=sender.id, root_id='root', visited=[], hops=0,
                    state='running', result='', error='')
    board._request = lambda *args: {'receipts': [incoming]}
    settle = board._settle

    async def settle_then_replace_source(node, receipt):
        await settle(node, receipt)
        await asyncio.sleep(0)  # forwarding/group delivery can yield here
        if change == 'generation':
            sender.launch_generation += 1
        elif change == 'token':
            sender.bridge_token = 'rotated-session-token'
        elif change == 'state':
            sender.state = 'stopping'
        else:
            board.nodes[sender.id] = Node(sender.id, 'codex', '', 'Replacement', layout({}), state='ready')

    board._settle = settle_then_replace_source
    with pytest.raises(PermissionError):
        await board.bridge(sender.bridge_token,
                           dict(operation='send', target='native', prompt='Hello', id='outgoing', root_id='root'))
    assert not board.handoffs and set(board.receipts) == {'root'}
    assert not board.nodes['native'].input_pending


async def test_actual_tool_http_native_and_workflow_delivery(tmp_path, monkeypatch):
    runtime, workspace = tmp_path / 'runtime', tmp_path / 'workspace'
    runtime.mkdir()
    workspace.mkdir()
    monkeypatch.setattr(config, 'ROOT', runtime)
    queue = []
    messages = LinkedMessages('workflow')
    receiver = StudioServer(EventBus(), session={'session_id': 'receiver', 'workspace': str(workspace)},
                            on_link_message=lambda envelope: messages.accept(envelope, queue.append),
                            on_link_receipts=messages.receipts)
    parent = StudioServer(EventBus(), session={'session_id': 'parent', 'workspace': str(workspace)})
    try:
        receiver_url = await asyncio.wait_for(receiver.start(), timeout=5)
        parent_url = await asyncio.wait_for(parent.start(), timeout=5)
        board = parent._board()
        for key, profile in [('dream', 'dream'), ('native', 'native'), ('workflow', 'dream')]:
            board.nodes[key] = Node(key, 'codex', '', key.title(), layout({}), profile=profile, state='ready')
        board.nodes['workflow'].address = (receiver_url.split('/#')[0], receiver.token)
        board._link('dream', 'native', False)
        board._link('dream', 'workflow', False)
        board._link('native', 'dream', False)
        sender = board.nodes['dream']
        bridge_url = parent_url.split('/#')[0] + '/api/deepsleep/bridge'
        monkeypatch.setenv('DREAM_DEEPSLEEP_NODE_ID', sender.id)
        monkeypatch.setenv('DREAM_DEEPSLEEP_BRIDGE_TOKEN', sender.bridge_token)
        monkeypatch.setenv('DREAM_DEEPSLEEP_BRIDGE_URL', bridge_url)
        # Production tools live in a child process; do not mark this fixture's
        # in-process parent server as a child when exercising its real routes.
        tool_configuration = session_tools._configuration()
        monkeypatch.delenv('DREAM_DEEPSLEEP_NODE_ID')
        monkeypatch.setattr(session_tools, '_configuration', lambda: tool_configuration)
        listing = unpack(await session_tools.session_list.handler({}))
        assert {n['id'] for n in listing['nodes']} == {'dream', 'native', 'workflow'}
        request = dict(target='native', prompt='Hello native receiver', id='native-message')
        manual = unpack(await session_tools.session_send.handler(request))
        assert manual['state'] == 'prepared' and manual['target'] == 'native'
        assert unpack(await session_tools.session_send.handler(request)) == manual
        assert len(board.handoffs) == 1 and not queue and not board.nodes['native'].input_pending
        conflict = await session_tools.session_send.handler(dict(request, prompt='changed'))
        assert conflict['is_error'] and 'different content' in str(conflict)
        task_request = dict(target='workflow', prompt='Hello workflow receiver', id='workflow-message')
        task = unpack(await session_tools.session_send.handler(task_request))
        assert task['state'] == 'queued' and task['target'] == 'workflow'
        assert unpack(await session_tools.session_send.handler(task_request)) == task
        assert len(queue) == 1 and 'Hello workflow receiver' in queue[0].text
        async with httpx.AsyncClient(trust_env=False) as client:
            # Native UI uses the owner-scoped action route, not a provider mailbox.
            result = await client.post(parent_url.split('/#')[0] + '/api/deepsleep',
                headers={'X-Dream-Token': parent.token},
                json=dict(action='handoff', source='native', target='dream', prompt='Native reply', id='reverse'))
            assert result.status_code == 200
            assert result.json()['handoffs'][-1]['target'] == 'dream'
            unauthorized = await client.post(bridge_url, headers={'X-Dream-Token': 'wrong'}, json={'operation': 'list'})
            assert unauthorized.status_code == 401
            malformed = await client.post(bridge_url, headers={'X-Dream-Token': sender.bridge_token}, json=[])
            assert malformed.status_code == 400
            control = await client.post(parent_url.split('/#')[0] + '/api/deepsleep',
                headers={'X-Dream-Token': sender.bridge_token}, json={'action': 'close', 'node': 'native'})
            assert control.status_code == 401
        reverse = unpack(await session_tools.session_inbox.handler({'id': 'reverse'}))['receipts'][0]
        assert reverse['state'] == 'prepared' and 'Native reply' in reverse['text']
        board.links = {k: v for k, v in board.links.items() if v['target'] != 'native'}
        unlinked = await session_tools.session_send.handler(dict(request, id='unlinked'))
        assert unlinked['is_error'] and 'directed link' in str(unlinked)
        assert len(queue) == 1 and not board.nodes['dream'].input_pending
    finally:
        await parent.stop()
        await receiver.stop()
