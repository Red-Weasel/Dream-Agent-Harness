"""Authenticated guided task endpoints. No endpoint accepts executable documents."""
from __future__ import annotations

import json
from starlette.concurrency import run_in_threadpool
from starlette.responses import JSONResponse, Response
from starlette.routing import Route
from ..workflows import WorkflowService
from ..workflows.service import RECIPES

MAX_JSON = 32 * 1024


def routes(server):
    def service():
        workspace = server._workspace()
        if workspace is None:
            raise ValueError('Choose a workspace before creating guided tasks')
        return WorkflowService(workspace)

    def guard(request):
        if not server._authorized(request):
            return JSONResponse({'error': 'unauthorized'}, status_code=401)
        origin = request.headers.get('origin')
        if request.method == 'POST' and origin and origin.rstrip('/') != str(request.base_url).rstrip('/'):
            return JSONResponse({'error': 'origin does not match this Studio session'}, status_code=403)

    async def endpoint(request):
        refused = guard(request)
        if refused is not None:
            return refused
        action = request.path_params['action']
        if request.method == 'GET' and action not in {'recipes', 'list'}:
            return JSONResponse({'error': 'This action requires POST'}, status_code=405)
        try:
            payload = {}
            if request.method == 'POST':
                data = bytearray()
                async for chunk in request.stream():
                    if len(data) + len(chunk) > MAX_JSON:
                        return JSONResponse({'error': 'Task request exceeds 32 KiB'}, status_code=413)
                    data.extend(chunk)
                payload = json.loads(data)
                if not isinstance(payload, dict):
                    raise ValueError('Expected a task object')
            svc = await run_in_threadpool(service)
            if action == 'recipes':
                result = {'recipes': RECIPES}
            elif action == 'list':
                result = {'tasks': await run_in_threadpool(svc.list), 'agent_available': callable(getattr(server, '_workflow_dispatch', None))}
            elif action == 'create':
                result = {'task': await run_in_threadpool(svc.create, payload['recipe'], payload['inputs'])}
            elif action == 'start':
                dispatch = getattr(server, '_workflow_dispatch', None)
                if not callable(dispatch):
                    return JSONResponse({'error': 'No agent is connected. Choose an agent, then start this reviewed task.'}, status_code=503)
                result = {'task': await svc.start(payload['task_id'], payload['expected_version'], payload['request_id'], dispatch)}
            elif action in {'recover', 'revise', 'check'}:
                result = {'task': await run_in_threadpool(getattr(svc, action), payload['task_id'], payload['expected_version'])}
            else:
                raise ValueError('Unknown guided task action')
            return JSONResponse(result, headers={'Cache-Control': 'no-store'})
        except (ValueError, KeyError, TypeError, OSError) as exc:
            status = 409 if 'Task changed' in str(exc) else 400
            return JSONResponse({'error': str(exc)}, status_code=status)

    async def artifact(request):
        refused = guard(request)
        if refused is not None:
            return refused
        try:
            svc = await run_in_threadpool(service)
            name, data = await run_in_threadpool(svc.artifact, request.path_params['task_id'], request.path_params['artifact_id'])
            return Response(data, media_type='application/octet-stream', headers={
                'Content-Disposition': f'attachment; filename="{name}"', 'Cache-Control': 'no-store',
                'X-Content-Type-Options': 'nosniff', 'Content-Security-Policy': "default-src 'none'; sandbox"})
        except (ValueError, KeyError, OSError) as exc:
            return JSONResponse({'error': str(exc)}, status_code=400)

    async def evidence(request):
        refused = guard(request)
        if refused is not None:
            return refused
        from .. import demonstrations as demos
        identifier = request.path_params['identifier']
        try:
            if request.method == 'GET':
                info = await run_in_threadpool(demos.read, identifier)
                result = await run_in_threadpool(demos.read_evidence, identifier)
                return JSONResponse({**result, 'frames': info.get('frames', [])}, headers={'Cache-Control': 'no-store'})
            data = bytearray()
            async for chunk in request.stream():
                if len(data) + len(chunk) > 512_000:
                    return JSONResponse({'error': 'Evidence request exceeds 512 KB'}, status_code=413)
                data.extend(chunk)
            payload = json.loads(data)
            if not isinstance(payload, dict) or type(payload.get('revision')) is not int:
                raise ValueError('Evidence requires the revision you reviewed')
            result = await run_in_threadpool(demos.save_evidence, identifier, payload,
                                             expected_revision=payload['revision'])
            return JSONResponse({'ok': True, 'result': result}, headers={'Cache-Control': 'no-store'})
        except (ValueError, KeyError, TypeError, OSError) as exc:
            return JSONResponse({'error': str(exc)}, status_code=409 if 'changed' in str(exc) else 400)

    async def evidence_frame(request):
        refused = guard(request)
        if refused is not None:
            return refused
        from .. import demonstrations as demos
        try:
            identifier = request.path_params['identifier']
            info = await run_in_threadpool(demos.read, identifier)
            file = 'frames/' + request.path_params['filename']
            if file not in {f['file'] for f in info.get('frames', [])}:
                raise ValueError('Frame is not in this recording')
            folder = demos.directory(identifier)
            path = folder / file
            if path.is_symlink() or not path.resolve().is_relative_to(folder.resolve()) or path.stat().st_size > 10_000_000:
                raise ValueError('Invalid frame')
            data = await run_in_threadpool(path.read_bytes)
            return Response(data, media_type='image/jpeg', headers={'Cache-Control': 'no-store', 'X-Content-Type-Options': 'nosniff'})
        except (ValueError, KeyError, OSError) as exc:
            return JSONResponse({'error': str(exc)}, status_code=400)

    return [Route('/api/learning/{identifier}/evidence', evidence, methods=['GET', 'POST']),
            Route('/api/learning/{identifier}/frames/{filename}', evidence_frame),
            Route('/api/workflows/artifacts/{task_id}/{artifact_id}', artifact),
            Route('/api/workflows/{action}', endpoint, methods=['GET', 'POST'])]
