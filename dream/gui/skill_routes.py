"""Authenticated full-text skill editing; saves grant no tool execution rights."""
import json

from starlette.concurrency import run_in_threadpool
from starlette.responses import JSONResponse
from starlette.routing import Route

from ..skills import editor

MAX_JSON = 768_000  # Includes worst-case JSON escaping of the bounded UTF-8 document.


def routes(server):
    async def endpoint(request):
        if not server._authorized(request):
            return JSONResponse({'error': 'unauthorized'}, status_code=401)
        origin = request.headers.get('origin')
        if request.method == 'POST' and origin and origin.rstrip('/') != str(request.base_url).rstrip('/'):
            return JSONResponse({'error': 'Origin does not match this session.'}, status_code=403)
        try:
            name = request.path_params.get('name')
            if request.method == 'GET':
                result = await run_in_threadpool(editor.read, name) if name else await run_in_threadpool(editor.listing)
            else:
                data = bytearray()
                async for chunk in request.stream():
                    if len(data) + len(chunk) > MAX_JSON:
                        return JSONResponse({'error': 'Skill request exceeds 768000 bytes.'}, status_code=413)
                    data.extend(chunk)
                payload = json.loads(data)
                if not isinstance(payload, dict):
                    raise ValueError('Expected a skill object.')
                if name:
                    result = await run_in_threadpool(editor.save, name, payload.get('content'), payload.get('expected_sha256'))
                else:
                    result = await run_in_threadpool(editor.create, payload.get('name'), payload.get('content'))
            return JSONResponse(result, headers={'Cache-Control': 'no-store'})
        except editor.Conflict as exc:
            return JSONResponse({'error': str(exc)}, status_code=409)
        except editor.NotFound as exc:
            return JSONResponse({'error': str(exc)}, status_code=404)
        except (ValueError, TypeError, KeyError, RecursionError) as exc:
            return JSONResponse({'error': str(exc)}, status_code=400)
        except OSError:
            return JSONResponse({'error': 'Cannot safely access the skill files. Check ownership and filesystem permissions.'}, status_code=400)
    return [Route('/api/skills', endpoint, methods=['GET', 'POST']),
            Route('/api/skills/{name}', endpoint, methods=['GET', 'POST'])]
