"""Authenticated saved-project metadata and read-only conversation endpoints."""
import json
import sqlite3
from pathlib import Path

from starlette.concurrency import run_in_threadpool
from starlette.responses import JSONResponse
from starlette.routing import Route

from ..projects import ProjectError, StaleRevision
from ..projects.library import ProjectLibrary


def routes(server):
    async def endpoint(request):
        if not server._authorized(request):
            return JSONResponse({'error': 'unauthorized'}, status_code=401)
        mutation = request.method in {'POST', 'PATCH'}
        origin = request.headers.get('origin')
        if mutation and origin and origin.rstrip('/') != str(request.base_url).rstrip('/'):
            return JSONResponse({'error': 'Origin does not match this session.'}, status_code=403)
        try:
            library = ProjectLibrary()
            project_id = request.path_params.get('project_id')
            session_id = request.path_params.get('session_id')
            if mutation:
                raw = bytearray()
                async for chunk in request.stream():
                    raw.extend(chunk)
                    if len(raw) > 96000:
                        return JSONResponse({'error': 'Project request exceeds 96 KiB.'}, status_code=413)
                payload = json.loads(raw)
                if not isinstance(payload, dict):
                    raise ProjectError('Expected a project object.')
                if project_id and request.url.path.endswith('/sessions'):
                    if set(payload) - {'session_id', 'confirmed_project'}:
                        raise ProjectError('Unknown conversation linking field.')
                    project = await run_in_threadpool(library.get, project_id)
                    current = server._session
                    if (payload.get('session_id') == current.get('session_id')
                            and project['workspace'] != current.get('workspace')):
                        raise ProjectError('The current conversation belongs to another active workspace.')
                    result = await run_in_threadpool(library.link_session, project_id, **payload)
                elif project_id:
                    result = {'project': await run_in_threadpool(library.update, project_id, **payload)}
                else:
                    if set(payload) - {'name', 'workspace', 'instructions'}:
                        raise ProjectError('Unknown project field.')
                    current = server._session
                    workspace = payload.get('workspace', current.get('workspace'))
                    associated = (current.get('session_id') if isinstance(workspace, str)
                                  and current.get('workspace')
                                  and Path(workspace).expanduser().resolve() == Path(current['workspace']).resolve() else None)
                    result = {'project': await run_in_threadpool(library.create, payload.get('name'), workspace,
                        payload.get('instructions', ''), session_id=associated)}
            elif request.url.path.endswith('/unassigned-sessions'):
                result = await run_in_threadpool(library.unassigned_sessions)
            elif session_id and request.url.path.endswith('/handoff'):
                result = await run_in_threadpool(library.handoff, project_id, session_id)
            elif session_id:
                result = await run_in_threadpool(library.transcript, project_id, session_id,
                    offset=int(request.query_params.get('offset', 0)), limit=int(request.query_params.get('limit', 100)))
            elif request.url.path.endswith('/memory'):
                result = await run_in_threadpool(library.memory, project_id)
            elif project_id:
                project = await run_in_threadpool(library.get, project_id)
                result = {'project': project, 'sessions': await run_in_threadpool(library.sessions, project_id)}
            else:
                result = {'projects': await run_in_threadpool(library.list)}
            return JSONResponse(result, headers={'Cache-Control': 'no-store'})
        except StaleRevision as exc:
            return JSONResponse({'error': str(exc)}, status_code=409)
        except (ValueError, TypeError, KeyError, OSError, sqlite3.Error, RecursionError) as exc:
            return JSONResponse({'error': str(exc)}, status_code=400)

    return [Route('/api/projects', endpoint, methods=['GET', 'POST']),
            Route('/api/projects/unassigned-sessions', endpoint, methods=['GET']),
            Route('/api/projects/{project_id}/memory', endpoint, methods=['GET']),
            Route('/api/projects/{project_id}/sessions', endpoint, methods=['POST']),
            Route('/api/projects/{project_id}/sessions/{session_id}/handoff', endpoint, methods=['GET']),
            Route('/api/projects/{project_id}/sessions/{session_id}', endpoint, methods=['GET']),
            Route('/api/projects/{project_id}', endpoint, methods=['GET', 'POST', 'PATCH'])]
