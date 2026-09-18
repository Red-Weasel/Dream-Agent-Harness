import asyncio
import json
import subprocess
import sys
import httpx
import pytest
from dream.media.service import MediaService
from dream.media.providers import ComfyUI, validate_workflow
from dream.media.store import MediaError


def run(coro):
    return asyncio.run(coro)


def test_lifecycle_and_handoff(tmp_path):
    s = MediaService(tmp_path)
    p = run(s.execute('create', {'title': 'Demo'}))
    assert p['revision'] == 1
    j = run(s.execute(
        'handoff',
        {'project_id': p['id'], 'provider': 'chatgpt', 'prompt': 'A cat', 'idempotency_key': 'x'},
    ))
    assert j['status'] == 'awaiting_user'
    assert j['request']['url'] == 'https://chatgpt.com/'
    assert run(s.execute(
        'handoff',
        {'project_id': p['id'], 'provider': 'chatgpt', 'prompt': 'A cat', 'idempotency_key': 'x'},
    ))['id'] == j['id']
    bad = tmp_path / 'bad.png'
    bad.write_text('not media')
    with pytest.raises(MediaError):
        run(s.execute('complete_handoff', {'job_id': j['id'], 'path': str(bad)}))
    from PIL import Image
    good = tmp_path / 'cat.png'
    Image.new('RGB', (16, 16)).save(good)
    result = run(s.execute('complete_handoff', {'job_id': j['id'], 'path': str(good)}))
    assert result['status'] == 'succeeded'
    assert MediaService(tmp_path).store.get_job(j['id']) == result


def test_html_worker_and_revision(tmp_path):
    s = MediaService(tmp_path)
    p = run(s.execute('create', {'title': 'Demo'}))
    j = run(s.execute(
        'render',
        {'project_id': p['id'], 'format': 'html', 'wait': True, 'idempotency_key': 'one'},
    ))
    assert j['status'] == 'succeeded', j
    assert s.store.asset_path(j['result']['asset_ids'][0]).read_text().startswith('<!doctype html>')
    assert run(s.execute(
        'render',
        {'project_id': p['id'], 'format': 'html', 'wait': True, 'idempotency_key': 'one'},
    ))['id'] == j['id']
    assert run(s.execute('get', {'project_id': p['id'], 'revision': 1}))['project']['revision'] == 1


def test_observation_does_not_recover(tmp_path):
    s = MediaService(tmp_path)
    p = s.store.create_project('x')
    j = s.store.create_job(p['id'], 'render', {})
    s.store.update_job(j['id'], 'running')
    run(s.execute('jobs', {}))
    s.status()
    assert s.store.get_job(j['id'])['status'] == 'running'
    assert run(s.execute('cancel', {'job_id': j['id']}))['status'] == 'cancel_requested'


def test_cli(tmp_path):
    result = subprocess.run(
        [
            sys.executable,
            '-m',
            'dream.media.cli',
            '--workspace',
            str(tmp_path),
            'create',
            '--title',
            'CLI',
        ],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout)['title'] == 'CLI'

@pytest.mark.parametrize(
    'url',
    [
        'https://evil.test',
        'http://localhost.evil:8188',
        'http://127.0.0.1:8188/foo',
        'http://user@127.0.0.1:8188',
    ],
)

def test_provider_loopback(url):
    with pytest.raises(MediaError):
        ComfyUI(url)

@pytest.mark.parametrize(
    'workflow',
    [
        {'1': {'class_type': 'PaidImage', 'inputs': {}}},
        {'1': {'class_type': 'CLIPTextEncode', 'inputs': {'text': 'https://evil.test'}}},
    ],
)

def test_workflow_refusal(workflow):
    with pytest.raises(MediaError):
        validate_workflow(workflow)


def test_provider_history_and_output(tmp_path):
    from PIL import Image
    import io
    buffer = io.BytesIO()
    Image.new('RGB', (16, 16)).save(buffer, format='PNG')

    def handler(req):
        if req.url.path == '/prompt':
            return httpx.Response(200, json={'prompt_id': 'abc'})
        if req.url.path == '/history/abc':
            return httpx.Response(
                200,
                json={
                    'abc': {
                        'status': {'completed': True, 'status_str': 'success'},
                        'outputs': {
                            '1': {'images': [{'filename': 'x.png', 'subfolder': '', 'type': 'output'}]},
                        },
                    },
                },
            )
        return httpx.Response(200, content=buffer.getvalue())

    async def case():
        c = ComfyUI(transport=httpx.MockTransport(handler))
        assert await c.submit({'1': {'class_type': 'SaveImage', 'inputs': {}}}, 'owner') == 'abc'
        paths = await c.outputs('abc', tmp_path)
        assert paths and paths[0].exists()
    run(case())


def test_background_worker_durable_and_duplicate(tmp_path):
    import time
    s = MediaService(tmp_path)
    p = run(s.execute('create', {'title': 'Background'}))
    request = {'project_id': p['id'], 'format': 'html', 'idempotency_key': 'background'}
    j = run(s.execute('render', request))
    assert run(s.execute('render', request))['id'] == j['id']
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        j = MediaService(tmp_path).store.get_job(j['id'])
        if j['status'] in {'succeeded', 'failed'}:
            break
        time.sleep(0.05)
    assert j['status'] == 'succeeded', j
    assert len(s.store.list_jobs()) == 1


def test_generate_uncertain_and_reconcile_never_submit(tmp_path):
    calls = []

    def handler(req):
        calls.append(req.method + ' ' + req.url.path)
        if req.method == 'POST':
            raise httpx.ReadTimeout('uncertain')
        return httpx.Response(200, json={})
    s = MediaService(tmp_path, provider_transport=httpx.MockTransport(handler))
    p = run(s.execute('create', {'title': 'Remote'}))
    payload = {
        'project_id': p['id'],
        'workflow': {'1': {'class_type': 'SaveImage', 'inputs': {}}},
        'wait': True,
        'resource_confirmed': True,
        'workflow_confirmed': True,
        'idempotency_key': 'unique',
    }
    j = run(s.execute('generate', payload))
    assert j['status'] == 'unknown'
    assert run(s.execute('generate', payload))['id'] == j['id']
    with pytest.raises(MediaError):
        run(s.execute('reconcile', {'job_id': j['id']}))
    assert calls == ['POST /prompt']


def test_provider_requires_confirmations(tmp_path):
    s = MediaService(tmp_path)
    p = run(s.execute('create', {'title': 'No load'}))
    with pytest.raises(MediaError, match='confirmation'):
        run(s.execute('generate', {'project_id': p['id'], 'workflow': {}}))
    assert s.store.list_jobs() == []


def test_provider_reconciliation_validates_import(tmp_path):
    import io
    from PIL import Image
    buffer = io.BytesIO()
    Image.new('RGB', (16, 16)).save(buffer, format='PNG')
    calls = []

    def handler(req):
        calls.append(req.method + ' ' + req.url.path)
        if req.url.path == '/history/abc':
            return httpx.Response(
                200,
                json={
                    'abc': {
                        'status': {'completed': True},
                        'outputs': {
                            '1': {'images': [{'filename': 'x.png', 'subfolder': '', 'type': 'output'}]},
                        },
                    },
                },
            )
        return httpx.Response(200, content=buffer.getvalue())
    s = MediaService(tmp_path, provider_transport=httpx.MockTransport(handler))
    p = s.store.create_project('x')
    j = s.store.create_job(p['id'], 'generate', {'base_url': 'http://127.0.0.1:8188'})
    s.store.update_job(j['id'], 'running', backend_id='abc')
    s.store.update_job(j['id'], 'unknown')
    result = run(s.execute('reconcile', {'job_id': j['id']}))
    assert result['status'] == 'succeeded'
    assert s.store.get_asset(result['result']['asset_ids'][0])['mime'] == 'image/png'
    assert all((x.startswith('GET ') for x in calls))


def test_export_embeds_only_revision_assets_with_correct_mime(tmp_path):
    from PIL import Image
    s = MediaService(tmp_path)
    p = run(s.execute('create', {'title': 'Image'}))
    path = tmp_path / 'source.png'
    Image.new('RGB', (16, 16)).save(path)
    asset = run(s.execute('import', {'project_id': p['id'], 'path': str(path)}))
    composition = p['composition']
    composition['scenes'][0]['asset_id'] = asset['id']
    run(s.execute('save', {'project_id': p['id'], 'composition': composition, 'expected_revision': 1}))
    extra = tmp_path / 'unused.png'
    extra.write_text('This should not be read')
    unused = s.store.import_asset(p['id'], extra)
    s.store.asset_path(unused['id']).unlink()
    j = run(s.execute('render', {'project_id': p['id'], 'format': 'html', 'wait': True}))
    assert j['status'] == 'succeeded', j
    html = s.store.asset_path(j['result']['asset_ids'][0]).read_text()
    assert 'data:image/png;base64,' in html
    assert unused['id'] not in html

@pytest.mark.parametrize(
    'inputs',
    [
        {'image': '../../secret.png'},
        {'ckpt_name': '/models/secret'},
        {'filename_prefix': '../../outside'},
    ],
)

def test_workflow_paths_cannot_escape(inputs):
    with pytest.raises(MediaError):
        validate_workflow({'1': {'class_type': 'LoadImage', 'inputs': inputs}})


def test_cancelled_job_cannot_start_or_complete(tmp_path):
    s = MediaService(tmp_path)
    p = s.store.create_project('x')
    j = s.store.create_job(p['id'], 'render', {})
    assert run(s.execute('cancel', {'job_id': j['id']}))['status'] == 'cancelled'
    assert run(s.run_job(j['id']))['status'] == 'cancelled'
    with pytest.raises(MediaError):
        run(s.execute('complete_handoff', {'job_id': j['id'], 'path': 'unused.png'}))


def test_failed_external_output_cannot_succeed(tmp_path):

    def handler(req):
        if req.url.path.startswith('/history/'):
            return httpx.Response(
                200,
                json={
                    'abc': {
                        'status': {'completed': True},
                        'outputs': {
                            '1': {'images': [{'filename': 'x.png', 'subfolder': '', 'type': 'output'}]},
                        },
                    },
                },
            )
        return httpx.Response(200, content=b'not a PNG')
    s = MediaService(tmp_path, provider_transport=httpx.MockTransport(handler))
    p = s.store.create_project('x')
    j = s.store.create_job(p['id'], 'generate', {'base_url': 'http://127.0.0.1:8188'})
    s.store.update_job(j['id'], 'running', backend_id='abc')
    s.store.update_job(j['id'], 'unknown')
    with pytest.raises(MediaError):
        run(s.execute('reconcile', {'job_id': j['id']}))
    assert s.store.get_job(j['id'])['status'] == 'unknown'
    assert s.store.list_assets(p['id']) == []


def test_reviewable_svd_template_has_image_to_video_path():
    from dream.media.providers import svd_workflow
    workflow = svd_workflow('svd_xt.safetensors', 'reference.png')
    validate_workflow(workflow)
    classes = {node['class_type'] for node in workflow.values()}
    assert {'ImageOnlyCheckpointLoader', 'SVD_img2vid_Conditioning', 'VideoLinearCFGGuidance', 'SaveWEBM'} <= classes
    assert workflow['7']['inputs']['fps'] == 6
    assert workflow['3']['inputs']['video_frames'] == 14
    with pytest.raises(MediaError):
        svd_workflow('../secret', 'reference.png')


def test_comfy_video_in_images_descriptor_is_imported(tmp_path):
    import shutil
    if not shutil.which('ffmpeg'):
        pytest.skip('ffmpeg unavailable')
    clip = tmp_path / 'fixture.webm'
    subprocess.run(
        [
            'ffmpeg',
            '-hide_banner',
            '-loglevel',
            'error',
            '-f',
            'lavfi',
            '-i',
            'color=c=blue:s=160x160:r=2:d=1',
            '-c:v',
            'libvpx-vp9',
            '-threads',
            '1',
            str(clip),
        ],
        check=True,
    )

    def handler(req):
        if req.url.path.startswith('/history/'):
            return httpx.Response(
                200,
                json={
                    'abc': {
                        'status': {'completed': True},
                        'outputs': {
                            '7': {
                                'images': [{'filename': 'clip.webm', 'subfolder': 'Dream', 'type': 'output'}],
                                'animated': [True],
                            },
                        },
                    },
                },
            )
        return httpx.Response(200, content=clip.read_bytes())
    s = MediaService(tmp_path, provider_transport=httpx.MockTransport(handler))
    p = s.store.create_project('Video')
    j = s.store.create_job(p['id'], 'generate', {'base_url': 'http://127.0.0.1:8188'})
    s.store.update_job(j['id'], 'running', backend_id='abc')
    s.store.update_job(j['id'], 'unknown')
    result = run(s.execute('reconcile', {'job_id': j['id']}))
    assert result['status'] == 'succeeded'
    assert s.store.get_asset(result['result']['asset_ids'][0])['mime'] == 'video/webm'


@pytest.mark.parametrize('cancel_during', ['submit', 'history'])
def test_cancelled_generation_observer_remains_unknown(tmp_path, cancel_during):
    async def case():
        reached_boundary = asyncio.Event()
        pending = asyncio.Event()
        requests = []
        first_history = True

        async def handler(request):
            nonlocal first_history
            requests.append((request.method, request.url.path))
            if request.method == 'POST':
                if cancel_during == 'submit':
                    reached_boundary.set()
                    await pending.wait()
                return httpx.Response(200, json={'prompt_id': 'owned-prompt'})
            if first_history:
                first_history = False
                reached_boundary.set()
                await pending.wait()
            return httpx.Response(200, json={})

        service = MediaService(tmp_path, provider_transport=httpx.MockTransport(handler))
        project = service.store.create_project('Cancelled observer')
        payload = {
            'project_id': project['id'],
            'workflow': {'1': {'class_type': 'SaveImage', 'inputs': {}}},
            'resource_confirmed': True,
            'workflow_confirmed': True,
            'idempotency_key': 'cancelled-observer',
            'wait': True,
        }
        task = asyncio.create_task(service.execute('generate', payload))
        await asyncio.wait_for(reached_boundary.wait(), timeout=5)
        task.cancel()
        result = await asyncio.wait_for(task, timeout=5)
        assert result['status'] == 'unknown'
        assert 'may still be running' in result['error']
        assert service.store.get_job(result['id']) == result
        assert result['backend_id'] == ('owned-prompt' if cancel_during == 'history' else None)
        assert (await service.execute('generate', payload))['id'] == result['id']
        if cancel_during == 'history':
            reconciled = await service.execute('reconcile', {'job_id': result['id']})
            assert reconciled['status'] == 'unknown'
            assert requests[-1] == ('GET', '/history/owned-prompt')
        else:
            with pytest.raises(MediaError, match='without backend ID'):
                await service.execute('reconcile', {'job_id': result['id']})
        assert sum(method == 'POST' for method, _ in requests) == 1

    run(case())


def test_explicit_recovery_stops_lost_queued_dispatch(tmp_path):
    service = MediaService(tmp_path)
    project = run(service.execute('create', {'title': 'Lost dispatcher'}))
    request = {'composition': project['composition'], 'revision': 1, 'format': 'html'}
    job = service.store.create_job(
        project['id'], 'render', request, idempotency_key='lost-dispatch',
    )
    work = service.store.root / 'tmp' / job['id']
    work.mkdir()
    marker = work / 'dispatched'
    marker.write_text('')
    observed = run(MediaService(tmp_path).execute('jobs', {}))
    assert observed['jobs'][0]['status'] == 'queued'
    recovered = run(MediaService(tmp_path).execute('recover', {}))
    assert recovered['jobs'][0]['status'] == 'interrupted'
    assert run(service.run_job(job['id']))['status'] == 'interrupted'
    duplicate = run(service.execute('render', {
        'project_id': project['id'], 'format': 'html', 'idempotency_key': 'lost-dispatch',
    }))
    assert duplicate['status'] == 'interrupted'
    assert marker.exists()
    assert not (work / 'output.html').exists()
