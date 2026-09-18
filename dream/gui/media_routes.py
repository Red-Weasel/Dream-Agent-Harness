"""Token-protected Create endpoints; preview documents never receive credentials."""
from __future__ import annotations

import base64
import json
import tempfile
from pathlib import Path

from starlette.responses import FileResponse, JSONResponse
from starlette.routing import Route

from ..media.composition import build_html, validate_composition
from ..media.service import MediaService
from ..media.store import MediaError

MAX_JSON = 1_048_576
MAX_UPLOAD = 128 * 1024 * 1024
MAX_PREVIEW = 24 * 1024 * 1024


def routes(server):
    def service():
        workspace = server._workspace()
        if workspace is None:
            raise MediaError('This Studio session has no workspace')
        return MediaService(workspace)

    def guard(request):
        if not server._authorized(request):
            return JSONResponse({'error': 'unauthorized'}, status_code=401)
        origin = request.headers.get('origin')
        if request.method == 'POST' and origin and origin.rstrip('/') != str(request.base_url).rstrip('/'):
            return JSONResponse({'error': 'origin does not match this Studio session'}, status_code=403)

    async def body(request):
        data = bytearray()
        async for chunk in request.stream():
            if len(data) + len(chunk) > MAX_JSON:
                raise OverflowError('Media request is too large')
            data.extend(chunk)
        payload = json.loads(data)
        if not isinstance(payload, dict):
            raise MediaError('Expected a media object')
        return payload

    async def endpoint(request):
        refused = guard(request)
        if refused is not None:
            return refused
        try:
            svc = service()
            action = request.path_params.get('action', '')
            if request.method == 'GET' and action not in {'status', 'projects', 'get', 'jobs', 'history', ''}:
                return JSONResponse({'error': 'This operation requires POST'}, status_code=405)
            if request.method == 'POST':
                payload = await body(request)
            else:
                payload = dict(request.query_params)
                payload.pop('token', None)
                if 'project_id' in request.path_params:
                    action = 'get'; payload['project_id'] = request.path_params['project_id']
            if action == 'validate':
                return JSONResponse({'composition': validate_composition(payload['composition'])},
                                    headers={'Cache-Control': 'no-store'})
            if action == 'preview':
                result = await svc.execute('get', payload)
                refs = {}; total = 0
                composition = result['project']['composition']
                wanted = {s.get('asset_id') for s in composition['scenes']} | {composition.get('audio_asset_id')}
                for asset in result['assets']:
                    if asset['id'] not in wanted:
                        continue
                    if not asset['mime'].startswith(('image/', 'video/', 'audio/')):
                        raise MediaError('Preview references must be images, video or audio')
                    total += asset['size']
                    if total > MAX_PREVIEW:
                        raise MediaError('Preview references exceed 24 MiB. Export the project to preview larger media.')
                    data = svc.store.asset_path(asset['id']).read_bytes()
                    refs[asset['id']] = 'data:' + asset['mime'] + ';base64,' + base64.b64encode(data).decode('ascii')
                return JSONResponse({'html': build_html(composition, refs)}, headers={'Cache-Control': 'no-store'})
            return JSONResponse(await svc.execute(action, payload), headers={'Cache-Control': 'no-store'})
        except OverflowError as exc:
            return JSONResponse({'error': str(exc)}, status_code=413)
        except (ValueError, KeyError, TypeError, OSError) as exc:
            return JSONResponse({'error': str(exc)}, status_code=400)

    async def upload(request):
        refused = guard(request)
        if refused is not None:
            return refused
        staged = None
        try:
            svc = service(); project_id = request.query_params['project_id']
            svc.store.get_project(project_id)
            name = request.query_params.get('filename', 'upload.bin')
            if not name or Path(name).name != name or '\\' in name or '\0' in name or len(name) > 200:
                raise MediaError('Choose a plain filename')
            # Server chooses the temporary directory; filename is only an asset label.
            with tempfile.TemporaryDirectory(prefix='upload-', dir=svc.store.root/'tmp') as temporary:
                staged = Path(temporary)/name
                with staged.open('xb') as out:
                    size = 0
                    async for chunk in request.stream():
                        size += len(chunk)
                        if size > MAX_UPLOAD:
                            raise OverflowError('Upload exceeds 128 MiB; use workspace import for larger files')
                        out.write(chunk)
                job_id = request.query_params.get('job_id')
                if job_id:
                    if svc.store.get_job(job_id)['project_id'] != project_id:
                        raise MediaError('Job belongs to another project')
                    result = await svc.execute('complete_handoff', {'job_id': job_id, 'path': str(staged)})
                else:
                    result = await svc.execute('import', {'project_id': project_id, 'path': str(staged), 'provenance': {'method': 'browser_upload'}})
                return JSONResponse(result)
        except OverflowError as exc:
            return JSONResponse({'error': str(exc)}, status_code=413)
        except (ValueError, KeyError, TypeError, OSError) as exc:
            return JSONResponse({'error': str(exc)}, status_code=400)

    async def asset(request):
        refused = guard(request)
        if refused is not None:
            return refused
        try:
            svc = service(); record = svc.store.get_asset(request.path_params['asset_id'])
            # SVG/HTML remain downloads, never executable on the Studio origin.
            inline = record['mime'].startswith(('image/', 'video/', 'audio/')) and record['mime'] != 'image/svg+xml'
            return FileResponse(svc.store.asset_path(record['id']), media_type=record['mime'],
                                filename=record['name'], content_disposition_type='inline' if inline else 'attachment',
                                headers={'Cache-Control': 'no-store', 'X-Content-Type-Options': 'nosniff',
                                         'Content-Security-Policy': "default-src 'none'; sandbox"})
        except (ValueError, KeyError, OSError) as exc:
            return JSONResponse({'error': str(exc)}, status_code=400)

    return [Route('/api/media/upload', upload, methods=['POST']),
            Route('/api/media/assets/{asset_id}', asset),
            Route('/api/media/projects/{project_id}', endpoint),
            Route('/api/media/{action}', endpoint, methods=['GET', 'POST'])]
