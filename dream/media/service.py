"""Shared durable media operations for CLI, Studio and model tools."""
import asyncio
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time
import uuid
from .store import MediaStore, MediaError
from .composition import default_composition, validate_composition
from .providers import HANDOFFS, ComfyUI, validate_workflow, validate_media
from .blender import blender_status, probe_blender


class MediaService:

    def __init__(self, workspace, *, provider_transport=None):
        self.store = MediaStore(Path(workspace))
        self.provider_transport = provider_transport

    def _backend(self, base_url):
        return ComfyUI(base_url, transport=self.provider_transport)

    def status(self):
        return {
            'workspace': str(self.store.workspace),
            'providers': {
                **HANDOFFS,
                'comfyui': {
                    'support': 'local_workflow',
                    'note': 'Requires running loopback server, reviewed workflow and operator resource confirmation. No models are loaded by status.',
                },
            },
            'renderer': {
                'support': 'native',
                'ffmpeg': bool(shutil.which('ffmpeg')),
                'ffprobe': bool(shutil.which('ffprobe')),
                'evidence_source': 'Host executable lookup only; this does not inspect the shell sandbox.',
                'execution_verified': None,
                'next_step': 'Before encoding, check the chosen encoder in the actual permitted executor. '
                             'A host path can exist while sandbox paths or shared libraries are unavailable.',
                'note': 'HTML export needs no encoder. PNG and video need Playwright Chromium; its launch is checked during export.',
                'formats': ['mp4', 'webm', 'png', 'html'],
            },
            'blender': {
                **blender_status(),
                'note': 'Executable presence does not establish Cycles registration, GPU compatibility or headless rendering. Missing nvidia-smi only means NVIDIA tooling is unavailable; it does not mean there is no GPU. Intel Arc uses the oneAPI backend for Cycles. Cycles can also render on CPU.',
                'selection_guidance': 'Check actual hardware, driver/device support and executor device access before selecting a render backend. Software GL is a slower CPU fallback for Eevee, not proof that the host lacks a GPU. Prefer a verified Cycles pipeline for physically based photorealism; use verified GPU acceleration when available and resources permit. Report fallback reasons rather than silently declaring no GPU.',
                'next_step': 'After actual resource preflight, media_create(action="probe_blender", payload={"resource_confirmed": true}) checks registration and engine selection without rendering. Do not invent confirmation.',
            },
        }

    def _work(self, job_id):
        path = self.store._safe(self.store.root / 'tmp' / job_id)
        path.mkdir(mode=0o700, exist_ok=True)
        return path

    def _spawn(self, job):
        # Atomic marker prevents duplicate dispatch from idempotent requests.
        # A lost worker is recovered explicitly, never replayed by status calls.
        work = self._work(job['id'])
        marker = self.store._safe(work / 'dispatched')
        try:
            fd = os.open(marker, os.O_CREAT | os.O_EXCL | os.O_WRONLY | os.O_NOFOLLOW, 0o600)
        except FileExistsError:
            return
        os.close(fd)
        try:
            with (work / 'worker.log').open('ab') as log:
                process = subprocess.Popen(
                    [
                        sys.executable,
                        '-m',
                        'dream.media.cli',
                        '--workspace',
                        str(self.store.workspace),
                        'worker',
                        '--job',
                        job['id'],
                    ],
                    stdin=subprocess.DEVNULL,
                    stdout=log,
                    stderr=log,
                    start_new_session=True,
                    close_fds=True,
                )
            # Reap without keeping request/event-loop tasks alive.
            import threading
            threading.Thread(target=process.wait, daemon=True).start()
        except Exception:
            marker.unlink(missing_ok=True)
            raise

    async def execute(self, action, payload):
        if not isinstance(payload, dict):
            raise MediaError('Payload must be an object')
        p = payload
        s = self.store
        if action == 'status':
            return self.status()
        if action == 'probe_blender':
            if p.get('resource_confirmed') is not True:
                raise MediaError('Operator resource preflight confirmation is required before starting Blender diagnostics')
            result = await probe_blender(resource_confirmed=True)
            if result.get('probe_status') != 'ok':
                raise MediaError(result.get('error') or 'Blender diagnostics did not complete')
            return result
        if action == 'history':
            limit = p.get('limit', 30)
            if isinstance(limit, str):
                limit = int(limit)
            return {'outputs': s.output_history(limit),
                    'scope': str(s.workspace), 'source': 'Dream media store',
                    'note': 'Registered immutable assets only; arbitrary workspace files are not automatically archived.'}
        if action == 'projects':
            return {'projects': s.list_projects()}
        if action == 'create':
            return s.create_project(
                p.get('title', 'Untitled'),
                validate_composition(p['composition']) if 'composition' in p else default_composition(),
            )
        if action == 'get':
            project = s.get_project(p['project_id'])
            if 'revision' in p:
                snapshot = s.get_revision(project['id'], p['revision'])
                project = {
                    **project,
                    'revision': snapshot['revision'],
                    'composition': snapshot['composition'],
                }
            return {'project': project, 'assets': s.list_assets(project['id'])}
        if action == 'save':
            return s.update_project(
                p['project_id'],
                validate_composition(p['composition']),
                p['expected_revision'],
            )
        if action == 'import':
            return s.import_asset(p['project_id'], Path(p['path']), provenance=p.get('provenance'))
        if action == 'jobs':
            return {'jobs': s.list_jobs(p.get('project_id'))}
        if action == 'recover':
            return {'jobs': s.recover_jobs()}
        if action == 'cancel':
            job = s.get_job(p['job_id'])
            if job['status'] in {'queued', 'awaiting_user'}:
                return s.update_job(job['id'], 'cancelled')
            if job['status'] == 'running':
                return s.update_job(
                    job['id'],
                    'cancel_requested',
                    error='Local cancellation requested; external execution is not interrupted',
                )
            return job
        if action == 'complete_handoff':
            with s.worker_lock():
                job = s.get_job(p['job_id'])
                if job['status'] != 'awaiting_user':
                    raise MediaError('Job is not awaiting a result')
                source = Path(p['path'])
                source = source if source.is_absolute() else s.workspace / source
                s._safe(source)
                validate_media(source)
                asset = s.import_asset(
                    job['project_id'],
                    source,
                    provenance={'job_id': job['id'], 'provider': job['request'].get('provider', 'blender')},
                )
                return s.update_job(
                    job['id'],
                    'succeeded',
                    progress=1,
                    result={'asset_ids': [asset['id']]},
                )
        if action == 'reconcile':
            with s.worker_lock():
                job = s.get_job(p['job_id'])
                if job['kind'] != 'generate' or job['status'] not in {'unknown', 'running', 'cancel_requested'}:
                    raise MediaError('Job does not need external reconciliation')
                if not job['backend_id']:
                    raise MediaError('Submission outcome unknown without backend ID; inspect ComfyUI manually. Never resubmit this job.')
                return await self._collect(job)
        if action in {'handoff', 'open_external'}:
            if action == 'handoff':
                provider = p['provider']
                if provider not in HANDOFFS:
                    raise MediaError('Unknown handoff provider')
                if not isinstance(p.get('prompt'), str) or not p['prompt'].strip():
                    raise MediaError('Handoff requires a prompt')
                request = {
                    'provider': provider,
                    'url': HANDOFFS[provider]['url'],
                    'prompt': p['prompt'],
                    'asset_ids': p.get('asset_ids', []),
                }
            else:
                path = Path(p['path'])
                path = path if path.is_absolute() else s.workspace / path
                path = s._safe(path)
                if path.suffix.lower() != '.blend' or not path.is_file():
                    raise MediaError('Choose an existing workspace .blend file')
                if not shutil.which('blender'):
                    raise MediaError('Blender is unavailable')
                request = {'path': str(path), 'provider': 'blender'}
            with s.worker_lock():
                job = s.create_job(
                    p['project_id'],
                    action,
                    request,
                    idempotency_key=p.get('idempotency_key'),
                )
                if job['status'] != 'queued':
                    return job
                job = s.update_job(job['id'], 'awaiting_user')
                if action == 'open_external':
                    try:
                        process = subprocess.Popen(
                            [shutil.which('blender'), '--disable-autoexec', request['path']],
                            stdin=subprocess.DEVNULL,
                            stdout=subprocess.DEVNULL,
                            stderr=subprocess.DEVNULL,
                            start_new_session=True,
                        )
                        import threading
                        threading.Thread(target=process.wait, daemon=True).start()
                    except OSError as exc:
                        return s.update_job(job['id'], 'failed', error=str(exc))
                return job
        if action in {'render', 'generate'}:
            if action == 'render':
                project = s.get_project(p['project_id'])
                revision = p.get('revision', project['revision'])
                composition = s.get_revision(project['id'], revision)['composition']
                fmt = p.get('format', 'mp4')
                if fmt not in {'mp4', 'webm', 'png', 'html'}:
                    raise MediaError('Invalid export format')
                request = {
                    'composition': validate_composition(composition),
                    'revision': revision,
                    'format': fmt,
                }
            else:
                if p.get('resource_confirmed') is not True or p.get('workflow_confirmed') is not True:
                    raise MediaError('Operator resource preflight and concrete workflow review confirmation are required')
                backend = self._backend(p.get('base_url', 'http://127.0.0.1:8188'))
                request = {
                    'workflow': validate_workflow(p['workflow']),
                    'base_url': backend.base_url,
                    'resource_confirmed': True,
                    'workflow_confirmed': True,
                }
            job = s.create_job(p['project_id'], action, request, idempotency_key=p.get('idempotency_key'))
            if job['status'] == 'queued':
                if p.get('wait'):
                    return await self.run_job(job['id'])
                self._spawn(job)
            return s.get_job(job['id'])
        raise MediaError('Unknown media action: ' + str(action))

    async def _collect(self, job):
        s = self.store
        directory = self._work(job['id']) / ('collect-' + uuid.uuid4().hex)
        directory.mkdir(mode=0o700)
        paths = await self._backend(job['request']['base_url']).outputs(job['backend_id'], directory)
        if paths is None:
            return s.get_job(job['id'])
        ids = [s.import_asset(
            job['project_id'],
            path,
            provenance={'job_id': job['id'], 'backend_id': job['backend_id'], 'provider': 'comfyui'},
        )['id'] for path in paths]
        return s.update_job(job['id'], 'succeeded', progress=1, result={'asset_ids': ids})

    async def run_job(self, id):
        s = self.store
        # Durable queue contenders wait for this workspace's owned worker.
        while True:
            lock = s.worker_lock()
            try:
                lock.__enter__()
                break
            except MediaError:
                if s.get_job(id)['status'] != 'queued':
                    return s.get_job(id)
                await asyncio.sleep(0.25)
        try:
            job = s.get_job(id)
            if job['status'] != 'queued':
                return job
            job = s.update_job(id, 'running')
            try:
                if job['kind'] == 'render':
                    from .render import render
                    work = self._work(id)
                    assets = {}
                    composition = job['request']['composition']
                    refs = {scene['asset_id'] for scene in composition['scenes'] if scene.get('asset_id')}
                    if composition.get('audio_asset_id'):
                        refs.add(composition['audio_asset_id'])
                    for asset_id in sorted(refs):
                        asset = s.get_asset(asset_id)
                        source = s.asset_path(asset_id)
                        if s._hash_file(source) != asset['sha256']:
                            raise MediaError('Reference asset content changed')
                        path = s._safe(work / (asset['id'] + Path(asset['name']).suffix))
                        if not path.exists():
                            os.link(s.asset_path(asset['id']), path)
                        assets[asset['id']] = path.as_uri()
                    last = [0.0]

                    def cancelled():
                        return s.get_job(id)['status'] == 'cancel_requested'

                    def progress(value):
                        now = time.monotonic()
                        if now - last[0] > 0.5 or value == 1:
                            current = s.get_job(id)
                            if current['status'] == 'running':
                                s.update_job(id, 'running', progress=value)
                            last[0] = now
                    fmt = job['request']['format']
                    output = work / ('output.' + fmt)
                    metadata = await render(
                        job['request']['composition'],
                        assets,
                        output,
                        format=fmt,
                        progress=progress,
                        cancelled=cancelled,
                    )
                    asset = s.import_asset(
                        job['project_id'],
                        output,
                        provenance={'job_id': id, 'revision': job['request']['revision']},
                    )
                    if cancelled():
                        return s.update_job(id, 'cancelled')
                    return s.update_job(
                        id,
                        'succeeded',
                        progress=1,
                        result={'asset_ids': [asset['id']], 'metadata': metadata},
                    )
                if job['kind'] == 'generate':
                    backend = self._backend(job['request']['base_url'])
                    # running is the persisted submission intent. Any failure at
                    # this boundary remains unknown to prevent duplicate inference.
                    try:
                        backend_id = await backend.submit(job['request']['workflow'], id)
                    except Exception as exc:
                        return s.update_job(id, 'unknown', error=str(exc))
                    current = s.get_job(id)
                    job = s.update_job(id, current['status'], backend_id=backend_id)
                    for _ in range(900):
                        if s.get_job(id)['status'] == 'cancel_requested':
                            return s.update_job(
                                id,
                                'unknown',
                                error='Stopped observing; ComfyUI may still be running. Reconcile explicitly.',
                            )
                        job = await self._collect(job)
                        if job['status'] == 'succeeded':
                            return job
                        await asyncio.sleep(2)
                    return s.update_job(id, 'unknown', error='Polling timed out; reconcile explicitly')
                raise MediaError('Unsupported worker job kind')
            except asyncio.CancelledError:
                if job['kind'] == 'generate':
                    # Cancelling this observer does not cancel backend inference,
                    # even if the submit response never reached this process.
                    return s.update_job(
                        id,
                        'unknown',
                        error=(
                            'Observer cancelled; ComfyUI may still be running. '
                            'Reconcile explicitly; without a backend ID, inspect '
                            'ComfyUI manually before creating another request.'
                        ),
                    )
                return s.update_job(id, 'cancelled')
            except Exception as exc:
                return s.update_job(
                    id,
                    'unknown' if job['kind'] == 'generate' else 'failed',
                    error=str(exc),
                )
        finally:
            lock.__exit__(None, None, None)
