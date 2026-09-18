"""Authenticated workspace search, explicit context and read-only recovery."""
import hashlib
import inspect
import json
import subprocess
from pathlib import Path

from starlette.concurrency import run_in_threadpool
from starlette.responses import JSONResponse, Response
from starlette.routing import Route

from ..projects import ProjectError, ProjectWorkspace, StaleRevision


def routes(server, recovery_provider=None):
    async def endpoint(request):
        if not server._authorized(request):
            return JSONResponse({'error': 'unauthorized'}, status_code=401)
        origin = request.headers.get('origin')
        if request.method == 'POST' and origin and origin.rstrip('/') != str(request.base_url).rstrip('/'):
            return JSONResponse({'error': 'Origin does not match this session.'}, status_code=403)
        try:
            workspace = server._workspace()
            if workspace is None:
                raise ProjectError('Choose a workspace first.')
            svc = ProjectWorkspace(workspace)
            action = request.path_params['action']
            if request.method == 'POST':
                raw = bytearray()
                async for chunk in request.stream():
                    raw.extend(chunk)
                    if len(raw) > 16384:
                        return JSONResponse({'error': 'Project request exceeds 16 KiB.'}, status_code=413)
                payload = json.loads(raw)
                if not isinstance(payload, dict):
                    raise ProjectError('Expected a project object.')
            else:
                payload = dict(request.query_params)
            if action in {'pin', 'unpin', 'revalidate'} and request.method != 'POST':
                return JSONResponse({'error': 'This operation requires POST.'}, status_code=405)
            if action == 'manifest':
                result = await run_in_threadpool(svc.manifest)
            elif action == 'search':
                result = await run_in_threadpool(svc.search, payload.get('query', ''))
            elif action == 'pin':
                result = await run_in_threadpool(svc.pin, kind=payload.get('kind'), text=payload.get('text'),
                    path=payload.get('path'), label=payload.get('label'), expected_revision=payload.get('expected_revision'))
            elif action == 'unpin':
                result = await run_in_threadpool(svc.remove_pin, payload.get('id'), payload.get('expected_revision'))
            elif action == 'revalidate':
                result = await run_in_threadpool(svc.refresh_pin, payload.get('id'), payload.get('expected_revision'))
            elif action == 'context':
                result = await run_in_threadpool(svc.context, payload.get('prompt', ''))
            elif action == 'recovery':
                if recovery_provider is None:
                    result = {'runs': [], 'available': False, 'message': 'Durable run records are not connected in this session.'}
                else:
                    runs = await run_in_threadpool(recovery_provider, svc.workspace)
                    if inspect.isawaitable(runs):
                        runs = await runs
                    result = {'runs': runs, 'available': True,
                              'message': 'Read-only valid run records; at most 200 directories, 2 MiB per ledger and 1 second, newest 30 matches. Corrupt or oversized ledgers are excluded. Resume requires an explicit command and reconciliation of uncertain actions.'}
            elif action in {'source', 'download'}:
                path = payload.get('path')
                data = await run_in_threadpool(svc.read_source, path)
                digest = hashlib.sha256(data).hexdigest()
                if payload.get('sha256') and payload['sha256'] != digest:
                    raise StaleRevision('Source changed. Search again before opening this result.')
                if action == 'download':
                    from urllib.parse import quote
                    return Response(data, media_type='application/octet-stream', headers={
                        'Content-Disposition': "attachment; filename*=UTF-8''" + quote(Path(path).name),
                        'Cache-Control': 'no-store', 'X-Content-Type-Options': 'nosniff',
                        'Content-Security-Policy': "default-src 'none'; sandbox"})
                offset = int(payload.get('offset', 0))
                if offset < 0 or offset > 50000:
                    raise ProjectError('Source offset must be between 0 and 50000.')
                value, partial = await run_in_threadpool(svc._extract, path, data)
                result = {'path': path, 'offset': offset, 'text': value[offset:offset + 4000],
                          'partial': partial or len(value) > offset + 4000, 'sha256': digest}
            else:
                return JSONResponse({'error': 'Unknown project operation.'}, status_code=404)
            return JSONResponse(result, headers={'Cache-Control': 'no-store'})
        except StaleRevision as exc:
            return JSONResponse({'error': str(exc)}, status_code=409)
        except (ValueError, TypeError, KeyError, OSError) as exc:
            return JSONResponse({'error': str(exc)}, status_code=400)
        except (TimeoutError, subprocess.TimeoutExpired):
            return JSONResponse({'error': 'Source extraction timed out. Use a smaller text export.'}, status_code=400)
    return [Route('/api/project/{action}', endpoint, methods=['GET', 'POST'])]
