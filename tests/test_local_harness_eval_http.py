"""Actual isolated Python/HTTP/phase execution with a synthetic local responder.

Never invokes an installed provider, inference engine, GPU, account or external
endpoint. Scripted inference exercises the harness, not real-model capability.
"""
import json
from pathlib import Path
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import Thread

import pytest

from dream.local_harness_eval import LocalEvalConfig, launch


@pytest.fixture
def responder(request):
    observed = []
    replies = []

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_GET(self):
            metadata = {'/health': {'status': 'ok', 'inflight': 0, 'queued': 0, 'lanes_active': 0},
                        '/props': {'default_generation_settings': {'n_ctx': 16384},
                                   'total_slots': 1, 'slot_ctx': 16384, 'prompt_cache_slots': 1},
                        '/v1/models': {'data': [{'id': 'synthetic-fixture'}]}}
            overrides = getattr(request, 'param', {})
            if '/health' in overrides:
                metadata['/health'] = overrides['/health']
            if '/props' in overrides:
                metadata['/props'] = overrides['/props']
            assert self.path in metadata
            body = json.dumps(metadata[self.path]).encode()
            self.send_response(overrides.get('status', 200))
            self.send_header('Content-Type', 'application/json')
            self.send_header('Content-Length', str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_POST(self):
            assert self.path == '/v1/chat/completions'
            payload = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
            observed.append(payload)
            assert not self.headers.get('Authorization')
            if len(observed) > len(replies):
                reply = {'content': 'STATUS: DONE'}
            else:
                reply = replies[len(observed) - 1]
            if 'call' in reply:
                name, args = reply['call']
                if callable(args):
                    args = args(payload)
                offered = {tool['function']['name'] for tool in payload['tools']}
                if 'tool_call' in offered:
                    name, args = 'tool_call', {'name': name, 'arguments': args}
                delta = {'tool_calls': [{'index': 0, 'id': f'call-{len(observed)}',
                         'function': {'name': name, 'arguments': json.dumps(args)}}]}
                finish = 'tool_calls'
            else:
                delta, finish = {'content': reply['content']}, 'stop'
            event = {'model': 'synthetic-fixture', 'choices': [{'delta': delta, 'finish_reason': finish}],
                     'usage': {'prompt_tokens': 100, 'completion_tokens': 10}}
            identity_events = ''.join('data: ' + json.dumps({'model': model}) + '\n\n'
                                      for model in reply.get('models', []))
            body = (identity_events + 'data: ' + json.dumps(event) + '\n\ndata: [DONE]\n\n').encode()
            self.send_response(200)
            self.send_header('Content-Type', 'text/event-stream')
            self.send_header('Content-Length', str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f'http://127.0.0.1:{server.server_port}/v1', replies, observed
    server.shutdown()
    server.server_close()
    thread.join(timeout=3)
    assert not thread.is_alive()


@pytest.mark.parametrize('mode', ['default', 'descriptions'])
@pytest.mark.parametrize('absolute_paths', [False, True])
@pytest.mark.parametrize('catalog_mode', ['fixture', 'builtin_tools'])
def test_isolated_local_http_task_has_actual_tools_review_check_and_usage(responder, tmp_path, mode, absolute_paths, catalog_mode):
    endpoint, replies, observed = responder
    totals = json.dumps({'totals': {'North, Central': '12.30', 'South': '20.00'}, 'rows': 4, 'excluded': 1})
    run = tmp_path / ('local-' + mode)
    path = lambda name: str(run / 'workspace' / name) if absolute_paths else name
    replies.extend([
        {'call': ('read_file', {'path': path('sales.csv')})},
        {'call': ('write_file', {'path': path('totals.json'), 'content': totals})},
        {'content': 'STATUS: DONE'},
        {'call': ('read_file', {'path': 'totals.json'})},
        {'content': 'VERDICT: PASS\nGAPS: none'},
    ])
    report = launch(LocalEvalConfig(endpoint, 'synthetic-fixture', mode, max_requests=8,
                                   max_wall_seconds=10, catalog_mode=catalog_mode), run)
    assert report['passed'] is True, (report, (run / 'process.log').read_text())
    assert report['process_exit'] == 0 and report['status'] == 'done'
    assert len(observed) == 5
    assert [row['recipient'] for row in report['requests']] == ['lead'] * 3 + ['reviewer'] * 2
    for payload, row in zip(observed, report['requests']):
        assert payload == json.loads((run / 'requests' / row['request']).read_text())
        assert row['usage'] == {'prompt_tokens': 100, 'completion_tokens': 10}
        assert row['status'] == 'returned' and row['component_tokens'] is None
    evidence = report['ledger_evidence']
    assert evidence['accepted_work_count'] == 1
    assert all(gate['qualified'] is False for gate in evidence['gates'])
    assert report['live_endurance_qualified'] is False
    assert report['serving_build_independently_verified'] is None
    assert report['catalog']['kind'] == catalog_mode
    assert set(report['catalog']['allowed']) == {'read_file', 'write_file'}
    if catalog_mode == 'builtin_tools':
        assert report['catalog']['count'] > 50 and 'run_bash' in report['catalog']['denied']
    assert json.loads((run / 'catalog.json').read_text()) == report['catalog']
    if mode == 'descriptions':
        assert report['discovery_report']['within_target'] is None
        assert report['discovery_report']['qualified'] is False
    assert report['tool_calls'][0]['arguments']['path'] == path('sales.csv')
    assert (run / 'workspace' / 'sales.csv').read_text().endswith('South,\n')
    assert json.loads((run / 'workspace' / 'totals.json').read_text())['excluded'] == 1


@pytest.mark.parametrize('mode', ['default', 'descriptions'])
@pytest.mark.parametrize('workflow', ['correct', 'missing', 'wrong', 'open-only'])
def test_packaged_workflow_selection_requires_actual_find_open_before_work(responder, tmp_path, mode, workflow):
    endpoint, replies, observed = responder
    if workflow != 'missing':
        if workflow != 'open-only':
            replies.append({'call': ('skill_find', {'query': 'CSV' if workflow == 'correct' else 'browser'})})
        replies.append({'call': ('skill_open', {'name': 'data-analysis' if workflow != 'wrong' else 'browser'})})
    totals = json.dumps({'totals': {'North, Central': '12.30', 'South': '20.00'}, 'rows': 4, 'excluded': 1})
    replies.extend([{'call': ('read_file', {'path': 'sales.csv'})},
                    {'call': ('write_file', {'path': 'totals.json', 'content': totals})},
                    {'content': 'STATUS: DONE'},
                    {'call': ('read_file', {'path': 'totals.json'})},
                    {'content': 'VERDICT: PASS\nGAPS: none'}])
    run = tmp_path / ('skills-' + workflow)
    report = launch(LocalEvalConfig(endpoint, 'synthetic-fixture', mode, max_requests=9,
                                   max_wall_seconds=10, catalog_mode='builtin_tools_skills'), run)
    assert report['passed'] is (workflow == 'correct'), (report, (run / 'process.log').read_text())
    assert report['skill_selection']['checked'] is (workflow == 'correct')
    assert report['catalog']['skills_loaded'] is True
    assert report['catalog']['skills']['count'] > 40
    assert set(report['catalog']['allowed']) == {'read_file', 'write_file', 'skill_find', 'skill_open', 'artifact_read'}
    initial = json.dumps(observed[0])
    assert 'Validate one small example manually' not in initial
    assert 'data-analysis/SKILL.md' in report['source_sha256'] or 'skills/data-analysis/SKILL.md' in report['source_sha256']
    assert not report['live_endurance_qualified']
    if workflow == 'correct':
        skill_call = next(row for row in report['tool_calls'] if row['name'] == 'skill_open')
        expected = (Path(__file__).resolve().parents[1] / 'skills/data-analysis/SKILL.md').read_text()
        assert skill_call['result']['content'][0]['text'] == expected
        assert expected in json.dumps(observed, ensure_ascii=False).replace('\\n', '\n')


@pytest.mark.parametrize('target', ['skill_file', 'skill_refresh', 'run_bash'])
def test_packaged_skills_do_not_enable_supporting_files_scripts_or_refresh(responder, tmp_path, target):
    endpoint, replies, observed = responder
    arguments = {'skill_file': {'name': 'data-analysis', 'path': 'references/examples.md'},
                 'skill_refresh': {}, 'run_bash': {'command': 'touch forbidden'}}[target]
    replies.extend([{'call': (target, arguments)}, {'content': 'STATUS: DONE'}])
    run = tmp_path / target
    report = launch(LocalEvalConfig(endpoint, 'synthetic-fixture', 'descriptions', max_requests=3,
                                   max_wall_seconds=5, catalog_mode='builtin_tools_skills'), run)
    assert report['passed'] is False
    assert any(row['name'] == target for row in report['refused_selections'])
    assert not (run / 'workspace/forbidden').exists()


@pytest.mark.parametrize('mode', ['default', 'descriptions'])
@pytest.mark.parametrize('foreign', [False, True])
def test_long_packaged_skill_offloads_exact_body_and_only_bound_artifact_pages_read(responder, tmp_path, mode, foreign):
    endpoint, replies, observed = responder
    original = (Path(__file__).resolve().parents[1] / 'skills/understand/SKILL.md').read_text()
    assert len(original.encode()) > 4096
    def page_arguments(payload):
        text = next(message['content'] for message in reversed(payload['messages'])
                    if message.get('role') == 'tool' and 'Dream saved the exact original' in message.get('content', ''))
        metadata = json.loads(text.split('\n', 2)[1])
        return {'handle': 'artifact://foreign-run/file' if foreign else metadata['handle'],
                'offset': 1500, 'limit': 4096, 'unit': 'characters'}
    totals = json.dumps({'totals': {'North, Central': '12.30', 'South': '20.00'}, 'rows': 4, 'excluded': 1})
    replies.extend([{'call': ('skill_open', {'name': 'understand'})},
                    {'call': ('artifact_read', page_arguments)},
                    {'call': ('skill_find', {'query': 'CSV'})},
                    {'call': ('skill_open', {'name': 'data-analysis'})},
                    {'call': ('read_file', {'path': 'sales.csv'})},
                    {'call': ('write_file', {'path': 'totals.json', 'content': totals})},
                    {'content': 'STATUS: DONE'},
                    {'call': ('read_file', {'path': 'totals.json'})},
                    {'content': 'VERDICT: PASS\nGAPS: none'}])
    run = tmp_path / 'long-workflow'
    report = launch(LocalEvalConfig(endpoint, 'synthetic-fixture', mode, max_requests=10,
                                   max_wall_seconds=12, catalog_mode='builtin_tools_skills'), run)
    assert report['passed'] is (not foreign), (report, (run / 'process.log').read_text())
    received = next(message['content'] for message in observed[1]['messages']
                    if message.get('role') == 'tool' and 'Dream saved the exact original' in message.get('content', ''))
    assert original not in received and original[:1500] in received
    call = next(row for row in report['tool_calls'] if row['name'] == 'artifact_read')
    result = call['result']
    if foreign:
        assert result['is_error'] is True
        assert 'artifact_read:' in result['content'][0]['text']
        assert isinstance(report['failure_message'], str) and report['failure_message'].strip()
    else:
        page = json.loads(result['content'][0]['text'].split('\n', 1)[1])
        assert page['offset'] == 1500 and page['next_offset'] > 1500
        assert page['text'] == original[1500:page['next_offset']]
        assert len(result['content'][0]['text'].encode()) <= 4096
    snapshots = json.loads((run / 'skills-snapshot.json').read_text())
    assert next(row['body'] for row in snapshots if row['name'] == 'understand') == original
    assert not report['live_endurance_qualified']


def test_model_done_and_reviewer_pass_without_artifact_do_not_pass(responder, tmp_path):
    endpoint, replies, observed = responder
    replies.extend([{'content': 'STATUS: DONE'}, {'content': 'VERDICT: PASS\nGAPS: none'}])
    report = launch(LocalEvalConfig(endpoint, 'synthetic-fixture', max_requests=3, max_wall_seconds=5), tmp_path / 'false-done')
    assert report['passed'] is False and report['process_exit'] != 0
    assert not report['live_endurance_qualified']
    assert len(observed) <= 3


@pytest.mark.parametrize('failure', ['wrong_totals', 'input_write', 'outside_write'])
def test_incorrect_artifact_or_out_of_scope_model_write_never_passes(responder, tmp_path, failure):
    endpoint, replies, observed = responder
    run = tmp_path / 'refused'
    outside = tmp_path / 'outside.txt'
    content = json.dumps({'totals': {'North, Central': '999', 'South': '20.00'}, 'rows': 4, 'excluded': 1})
    destination = {'wrong_totals': 'totals.json', 'input_write': 'sales.csv',
                   'outside_write': str(outside)}[failure]
    replies.extend([{'call': ('read_file', {'path': 'sales.csv'})},
                    {'call': ('write_file', {'path': destination, 'content': content})},
                    {'content': 'STATUS: DONE'}, {'content': 'VERDICT: PASS\nGAPS: none'}])
    report = launch(LocalEvalConfig(endpoint, 'synthetic-fixture', max_requests=5, max_wall_seconds=5), run)
    assert report['passed'] is False and len(observed) <= 5
    assert not outside.exists()
    assert (run / 'workspace' / 'sales.csv').read_text().endswith('South,\n')
    if report['ledger_evidence'] is not None:
        assert report['ledger_evidence']['accepted_work_count'] == 0


def test_usage_observer_keeps_unknown_unknown_and_rejects_non_numeric_counts():
    from dream.local_harness_eval import _observe_sse
    row = {'usage': None, 'reported_model': None, 'stream_complete': False}
    _observe_sse(row, b'data: {"choices":[]}')
    assert row['usage'] is None
    _observe_sse(row, b'data: {"usage":{"prompt_tokens":true,"completion_tokens":"10","input_tokens":-1}}')
    assert row['usage'] is None
    _observe_sse(row, b'data: {"usage":{"prompt_tokens":0,"completion_tokens":10,"prompt_tokens_details":{"cached_tokens":4}}}')
    assert row['usage'] == {'prompt_tokens': 0, 'completion_tokens': 10, 'prompt_tokens_details': {'cached_tokens': 4}}
    _observe_sse(row, b'data:[DONE]')
    assert row['stream_complete'] is True


@pytest.mark.parametrize('models', [['different-model', 'synthetic-fixture'],
                                  ['synthetic-fixture', 'different-model', 'synthetic-fixture']])
def test_earlier_conflicting_stream_identity_fails_even_when_task_is_correct(responder, tmp_path, models):
    endpoint, replies, observed = responder
    totals = json.dumps({'totals': {'North, Central': '12.30', 'South': '20.00'}, 'rows': 4, 'excluded': 1})
    replies.extend([
        {'call': ('read_file', {'path': 'sales.csv'}), 'models': models},
        {'call': ('write_file', {'path': 'totals.json', 'content': totals})},
        {'content': 'STATUS: DONE'},
        {'call': ('read_file', {'path': 'totals.json'})},
        {'content': 'VERDICT: PASS\nGAPS: none'},
    ])
    run = tmp_path / 'mixed-identity'
    report = launch(LocalEvalConfig(endpoint, 'synthetic-fixture', max_requests=8, max_wall_seconds=10), run)
    assert report['ledger_evidence']['accepted_work_count'] == 1
    assert report['passed'] is False
    assert report['model_identity']['no_reported_mismatch'] is False
    assert report['requests'][0]['reported_model_conflict'] is True


@pytest.mark.parametrize('mode', ['default', 'descriptions'])
@pytest.mark.parametrize('name', ['run_bash', 'Bash', 'fork_verifier_agent', 'snip', 'task'])
def test_builtin_catalog_execution_and_dispatch_hatches_are_refused(responder, tmp_path, mode, name):
    endpoint, replies, observed = responder
    outside = tmp_path / 'must-not-exist.txt'
    replies.extend([{'call': (name, {'command': 'touch ' + str(outside), 'subagent_type': 'coder',
                                  'prompt': 'Do not dispatch a worker.', 'ranges': [[1, 2]]})},
                    {'content': 'STATUS: DONE'}, {'content': 'VERDICT: PASS\nGAPS: none'}])
    report = launch(LocalEvalConfig(endpoint, 'synthetic-fixture', mode, max_requests=4,
                                   max_wall_seconds=6, catalog_mode='builtin_tools'), tmp_path / 'denied-catalog')
    assert report['passed'] is False and not outside.exists()
    assert len(observed) <= 4
    if name in {'run_bash', 'Bash'}:
        assert any(row['name'] == 'run_bash' for row in report['refused_selections'])
    elif mode == 'default':
        assert any(row['name'] == name for row in report['refused_selections'])
    assert not report['catalog']['skills_loaded']


def test_reported_model_mismatch_does_not_pass_requested_model_evaluation(responder, tmp_path):
    endpoint, replies, observed = responder
    totals = json.dumps({'totals': {'North, Central': '12.30', 'South': '20.00'}, 'rows': 4, 'excluded': 1})
    replies.extend([{'call': ('read_file', {'path': 'sales.csv'})},
                    {'call': ('write_file', {'path': 'totals.json', 'content': totals})},
                    {'content': 'STATUS: DONE'}, {'call': ('read_file', {'path': 'totals.json'})},
                    {'content': 'VERDICT: PASS\nGAPS: none'}])
    report = launch(LocalEvalConfig(endpoint, 'different-requested-model', max_requests=8, max_wall_seconds=10),
                    tmp_path / 'model-mismatch')
    assert report['passed'] is False and report['process_exit'] != 0
    assert report['model_identity']['no_reported_mismatch'] is False
    assert report['ledger_evidence']['accepted_work_count'] == 1
    assert report['serving_build_independently_verified'] is None


@pytest.mark.parametrize('responder', [
    {'/health': {'inflight': 1, 'queued': 0, 'lanes_active': 0}},
    {'/health': {}}, {'/props': {}}, {'status': 503},
], indirect=True)
def test_busy_unavailable_or_unknown_context_refuses_before_inference(responder, tmp_path):
    endpoint, replies, observed = responder
    report = launch(LocalEvalConfig(endpoint, 'synthetic-fixture', max_wall_seconds=5), tmp_path / 'preflight-refused')
    assert report['passed'] is False and report['process_exit'] != 0
    assert observed == []
    assert report['ledger_evidence'] is None
    assert report['failure_message']
