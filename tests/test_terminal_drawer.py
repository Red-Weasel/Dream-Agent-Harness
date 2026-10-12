"""Authenticated child metadata, with real event accounting and artifact records."""
import asyncio
import json
from types import SimpleNamespace
from unittest.mock import Mock

import httpx
import pytest

from dream.core.backends.base import Event
from dream.gui.artifacts import ArtifactRegistry
from dream.gui.bus import EventBus
from dream.gui.deepsleep import Node, layout
from dream.gui.server import StudioServer
from dream.gui.terminal_activity import ActivityReader, LIMIT, drawer_metadata, project
from dream.tui.app import App


def section(items, **extra):
    return {'available': True, 'items': items, 'total': len(items), **extra}


def fixture_app(tmp_path):
    app = App(provider='machx', model='fixture', workspace=tmp_path)
    app.renderer = Mock()
    app._feed_cockpit = Mock()
    app._record_error = Mock()
    app.artifacts = ArtifactRegistry(tmp_path, 'child-session', app.bus.publish, store=tmp_path / 'registry')
    return app


def write_event(app, path, identifier, *, failure=False, pending=False):
    app._render_event(Event('tool_use', {'id': identifier, 'name': 'write_file', 'input': {'path': path, 'content': '# Report'}}))
    if not pending:
        if not failure:
            (app.workspace / path).write_text('# Report\nReal file bytes.')
        app._render_event(Event('tool_result', {'id': identifier, 'name': 'write_file', 'is_error': failure, 'content': 'failed' if failure else 'written'}))


async def test_real_confirmed_accounting_artifacts_and_child_route_do_not_guess_sources(tmp_path):
    app = fixture_app(tmp_path)
    (tmp_path / 'unrelated.md').write_text('# Not this session')
    earlier = ArtifactRegistry(tmp_path, 'earlier-session', lambda _: None, store=tmp_path / 'registry')
    earlier.observe(Event('tool_use', {'id': 'old', 'name': 'write_file', 'input': {'path': 'unrelated.md'}}))
    earlier.observe(Event('tool_result', {'id': 'old', 'is_error': False}))
    write_event(app, 'report.md', 'one')
    write_event(app, 'report.md', 'two')
    write_event(app, 'failed.md', 'failed', failure=True)
    write_event(app, 'pending.md', 'pending', pending=True)
    app.bus.publish(Event('user', 'Attached files: invented.pdf; https://example.test is only prose'))
    app.bus.publish(Event('agent_activity', {'run_id': 'run-1', 'agent': 'Reviewer', 'kind': 'status', 'status': 'running', 'model': 'fixture-model'}))
    server = StudioServer(app.bus, session={'session_id': 'child-session', 'workspace': str(tmp_path)},
                          artifacts=app.artifacts, session_drawer=app._studio_drawer_snapshot,
                          on_link_agents=app.linked_agents, on_prompt=Mock())
    # Uploaded alone does not mean submitted or inspected in this child.
    server._uploads['upload-only'] = {'name': 'unsubmitted.pdf', 'path': 'uploads/unsubmitted.pdf'}
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=server.app), base_url='http://127.0.0.1') as client:
        response = await client.get('/api/conversation', headers={'X-Dream-Token': server.token})
        assert response.status_code == 200 and response.headers['cache-control'] == 'no-store'
        data = response.json()
    metadata = data['session']
    assert metadata['session_id'] == 'child-session'
    assert metadata['changes']['items'] == [{'path': 'report.md'}]
    assert metadata['changes']['total'] == 1
    assert len(metadata['outputs']['items']) == 1
    artifact = metadata['outputs']['items'][0]
    assert artifact['path'] == 'report.md' and artifact['type'] == 'report'
    assert artifact['version'] == 1  # Rewriting identical bytes does not invent a version.
    assert metadata['agents']['items'] == [{'id': 'run-1', 'name': 'Reviewer', 'model': 'fixture-model', 'state': 'running'}]
    assert 'not a process liveness check' in metadata['agents']['reason']
    assert not metadata['sources']['available'] and metadata['sources']['items'] == []
    assert project(data)['session'] == metadata
    assert [row['id'] for row in project(data)['events']] == [row['id'] for row in data['events']]
    server._on_prompt.assert_not_called()
    app._reset_session_accounting()
    assert app._studio_drawer_snapshot()['changes']['total'] == 0


def test_drawer_projection_caps_items_bytes_and_total_response_without_losing_identity():
    data = {'session': {'session_id': 'fixture',
        'changes': section([{'path': f'file-{i}'} for i in range(130)]),
        'outputs': section([{'id': str(i), 'path': f'{i}/' + '界' * 1000, 'title': '界' * 200} for i in range(130)]),
        'agents': section([{'id': f'run-{i}', 'name': '界' * 200} for i in range(130)]),
        'sources': section([{'id': str(i), 'path': f'uploads/{i}', 'name': '界' * 200} for i in range(130)])},
        'events': [{'id': str(i), 'kind': 'tool_result', 'data': {'content': 'x' * 16000}} for i in range(400)]}
    result = project(data)
    assert len(json.dumps(result, ensure_ascii=True).encode()) < LIMIT
    assert result['trimmed']
    for key in ('changes', 'outputs', 'agents', 'sources'):
        item = result['session'][key]
        assert item['total'] == 130 and item['trimmed']
        assert 0 < len(item['items']) <= 100
    assert len(result['session']['changes']['items']) == 100
    assert result['session']['outputs']['items'][0]['path'] == '0/' + '界' * 1000
    assert project(result) == result


def test_metadata_whitelist_rejects_malformed_identity_and_never_carries_content_or_urls():
    raw = {'session_id': 'fixture', 'token': 'secret',
           'changes': section([{'path': 'exact/[literal].py', 'content': 'private'}, {'path': 'x' * 4097}, None]),
           'outputs': section([{'id': 'artifact-1', 'path': 'report.html', 'title': '<b>Literal title</b>',
                                'content': '<script>execute()</script>', 'url': 'javascript:execute()',
                                'frame': {}, 'token': 'secret', 'bytes': True, 'at': float('inf')}]),
           'agents': {'available': True, 'items': 'malformed'},
           'sources': {'available': False, 'items': [{'id': 'fake', 'path': 'fake'}]}}
    clean = drawer_metadata(raw)
    assert clean['changes']['items'] == [{'path': 'exact/[literal].py'}]
    assert clean['changes']['total'] == 3 and clean['changes']['trimmed']
    assert clean['outputs']['items'] == [{'id': 'artifact-1', 'title': '<b>Literal title</b>', 'path': 'report.html'}]
    assert not clean['agents']['available'] and not clean['sources']['available']
    assert 'secret' not in json.dumps(clean) and 'execute' not in json.dumps(clean)


async def test_real_child_and_parent_projections_use_same_authenticated_owned_read(tmp_path, monkeypatch):
    monkeypatch.delenv('DREAM_DEEPSLEEP_NODE_ID', raising=False)
    snapshot = Mock(return_value={'changes': section([{'path': 'child.py'}])})
    child = StudioServer(EventBus(), session={'session_id': 'child'}, session_drawer=snapshot)
    parent = StudioServer(EventBus(), session={'session_id': 'parent', 'workspace': str(tmp_path)},
                          session_drawer=lambda: {'changes': section([{'path': 'wrong-parent.py'}])})
    node = Node('node-1', 'codex', '', 'Fixture', layout({}), state='ready',
                process=SimpleNamespace(returncode=None), address=('http://127.0.0.1:8124', child.token), launch_generation=4)
    parent._board().nodes[node.id] = node
    actual_client = httpx.AsyncClient
    async with actual_client(transport=httpx.ASGITransport(app=parent.app), base_url='http://127.0.0.1:8123') as client:
        monkeypatch.setattr('dream.gui.terminal_activity.httpx.AsyncClient',
                            lambda **kwargs: actual_client(transport=httpx.ASGITransport(app=child.app), **kwargs))
        for suffix in ('', '?token=' + parent.token):
            assert (await client.get('/api/conversation' + suffix)).status_code == 401
        assert (await client.get('/api/deepsleep/activity?node=node-1')).status_code == 401
        snapshot.assert_not_called()
        headers = {'X-Dream-Token': parent.token}
        result = await client.get('/api/deepsleep/activity?node=node-1', headers=headers)
        assert result.status_code == 200
        body = result.json()
        assert body['session']['session_id'] == 'child' and body['generation'] == '4'
        assert body['session']['changes']['items'] == [{'path': 'child.py'}]
        assert child.token not in result.text
        assert len(result.content) < LIMIT
        again = await client.get('/api/deepsleep/activity?node=node-1', headers=headers)
        assert again.json() == body and snapshot.call_count == 1
        node.profile = 'native'
        native = (await client.get('/api/deepsleep/activity?node=node-1', headers=headers)).json()
        assert not native['available'] and native['generation'] == '4'
        assert native['session']['sources']['available'] and native['session']['sources']['items'] == []
        assert snapshot.call_count == 1
    await parent._terminal_activity.close()


async def test_restart_during_drawer_read_discards_all_prior_session_metadata():
    gate, started = asyncio.Event(), asyncio.Event()
    async def read(*_):
        started.set()
        await gate.wait()
        return project({'events': [], 'session': {'session_id': 'old', 'changes': section([{'path': 'old.py'}])}})
    reader = ActivityReader(read)
    node = Node('node-1', 'codex', '', 'Fixture', layout({}), state='ready',
                process=SimpleNamespace(returncode=None), address=('http://127.0.0.1:8124', 'private'))
    board = SimpleNamespace(nodes={node.id: node})
    pending = asyncio.create_task(reader.get(board, node))
    await asyncio.wait_for(started.wait(), 1)
    node.launch_generation += 1
    gate.set()
    result = await asyncio.wait_for(pending, 1)
    assert not result['available'] and 'old.py' not in json.dumps(result)
    await reader.close()


def test_app_change_count_remains_complete_when_display_is_capped(tmp_path):
    app = fixture_app(tmp_path)
    app.changed_files = {f'file-{i:04}.py' for i in range(400)}
    snapshot = app._studio_drawer_snapshot()
    metadata = drawer_metadata(snapshot)
    assert metadata['changes']['total'] == 400
    assert len(metadata['changes']['items']) == 100 and metadata['changes']['trimmed']
    assert len(app.changed_files) == 400


async def test_successful_native_uploads_are_scoped_to_node_and_generation(tmp_path, monkeypatch):
    monkeypatch.delenv('DREAM_DEEPSLEEP_NODE_ID', raising=False)
    server = StudioServer(EventBus(), session={'workspace': str(tmp_path)})
    board = server._board()
    native = Node('native', 'codex', '', 'Native', layout({}), profile='native', launch_generation=5)
    other = Node('other', 'codex', '', 'Other', layout({}), profile='native')
    board.nodes.update(native=native, other=other)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=server.app), base_url='http://127.0.0.1') as client:
        url = '/api/deepsleep/upload?node=native&name=reference.txt'
        assert (await client.post(url, content=b'not saved')).status_code == 401
        assert not (tmp_path / 'uploads').exists()
        headers = {'X-Dream-Token': server.token, 'Content-Type': 'application/octet-stream'}
        receipt = (await client.post(url, content=b'Real attached bytes', headers=headers)).json()
        assert receipt['ok'] and (tmp_path / receipt['path']).read_bytes() == b'Real attached bytes'
        result = (await client.get('/api/deepsleep/activity?node=native', headers=headers)).json()
        assert result['available'] is False and result['generation'] == '5'
        sources = result['session']['sources']
        assert sources['available'] and sources['total'] == 1
        assert sources['items'] == [{key: receipt[key] for key in ('id', 'name', 'path', 'size')}]
        assert 'does not confirm submission or reading' in sources['reason']
        assert all(not result['session'][key]['available'] for key in ('changes', 'outputs', 'agents'))
        unrelated = (await client.get('/api/deepsleep/activity?node=other', headers=headers)).json()
        assert unrelated['generation'] == '0'
        assert unrelated['session']['sources']['available']
        assert unrelated['session']['sources']['items'] == []
        assert unrelated['session']['sources']['total'] == 0
        native.launch_generation += 1
        restarted = (await client.get('/api/deepsleep/activity?node=native', headers=headers)).json()
        assert restarted['available'] is False and restarted['generation'] == '6'
        assert restarted['session']['sources']['available']
        assert restarted['session']['sources']['items'] == []
        assert restarted['session']['sources']['total'] == 0
        assert not restarted['session']['sources']['trimmed']
        assert receipt['id'] not in json.dumps(restarted) and not server._drawer_sources
        assert (tmp_path / receipt['path']).exists()  # Restart clears display association, not user files.


@pytest.mark.parametrize('during', ['receive', 'save'])
async def test_upload_restart_never_attaches_to_replacement_generation(tmp_path, monkeypatch, during):
    monkeypatch.delenv('DREAM_DEEPSLEEP_NODE_ID', raising=False)
    server = StudioServer(EventBus(), session={'workspace': str(tmp_path)})
    board = server._board()
    node = Node('fixture', 'codex', '', 'Fixture', layout({}), profile='native')
    board.nodes[node.id] = node
    if during == 'receive':
        async def receive(_):
            node.launch_generation += 1
            return 'reference.txt', b'bytes'
        monkeypatch.setattr('dream.gui.uploads.receive_file', receive)
    else:
        async def save(function, *args):
            record = function(*args)
            node.launch_generation += 1
            return record
        monkeypatch.setattr('dream.gui.server.run_in_threadpool', save)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=server.app), base_url='http://127.0.0.1') as client:
        response = await client.post('/api/deepsleep/upload?node=fixture&name=reference.txt', content=b'bytes',
                                     headers={'X-Dream-Token': server.token, 'Content-Type': 'application/octet-stream'})
        assert response.status_code == 409
        assert not server._drawer_sources
        assert (tmp_path / 'uploads/reference.txt').exists() is (during == 'save')


async def test_parent_upload_merge_rebudgets_events_and_caps_retained_source_records(tmp_path, monkeypatch):
    monkeypatch.delenv('DREAM_DEEPSLEEP_NODE_ID', raising=False)
    server = StudioServer(EventBus(), session={'workspace': str(tmp_path)})
    board = server._board()
    node = Node('fixture', 'codex', '', 'Fixture', layout({}), state='ready', launch_generation=1,
                process=SimpleNamespace(returncode=None), address=('http://127.0.0.1:8124', 'private'))
    board.nodes[node.id] = node
    child = project({'events': [{'id': str(i), 'kind': 'text_delta', 'data': 'x' * 15000} for i in range(40)]})
    async def read(*_):
        return child
    server._terminal_activity = ActivityReader(read)
    for i in range(125):
        server._drawer_source_section(board, node, {'id': str(i), 'name': '界' * 120,
                                                   'path': f'uploads/{i}', 'size': 1})
    assert len(server._drawer_sources[node.id]['items']) == 100
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=server.app), base_url='http://127.0.0.1') as client:
        response = await client.get('/api/deepsleep/activity?node=fixture', headers={'X-Dream-Token': server.token})
        result = response.json()
        assert response.status_code == 200 and len(response.content) < LIMIT
        assert len(json.dumps(result, ensure_ascii=True).encode()) < LIMIT
        assert result['session']['sources']['total'] == 125
        assert result['session']['sources']['trimmed']
        assert result['session']['sources']['items'][0]['id'] == '25'
        assert result['events'][-1]['id'] == child['events'][-1]['id']
        assert len(result['events']) < len(child['events'])
    await server._terminal_activity.close()
