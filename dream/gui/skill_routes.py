"""Authenticated full-text skill editing; saves grant no tool execution rights."""
import json

from starlette.concurrency import run_in_threadpool
from starlette.responses import JSONResponse
from starlette.routing import Route

from .. import presets
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
                if request.url.path.endswith('/remove'):   # DREAM-171: delete yours, or remove your version
                    result = await run_in_threadpool(editor.remove, name, payload.get('expected_sha256'))
                elif name:
                    result = await run_in_threadpool(editor.save, name, payload.get('content'), payload.get('expected_sha256'))
                else:
                    result = await run_in_threadpool(editor.create, payload.get('name'), payload.get('content'))
            return JSONResponse(result, headers={'Cache-Control': 'no-store'})
        except (editor.Conflict, presets.Conflict) as exc:
            return JSONResponse({'error': str(exc)}, status_code=409)
        except editor.NotFound as exc:
            return JSONResponse({'error': str(exc)}, status_code=404)
        except (ValueError, TypeError, KeyError, RecursionError) as exc:
            return JSONResponse({'error': str(exc)}, status_code=400)
        except OSError:
            return JSONResponse({'error': 'Cannot safely access the skill files. Check ownership and filesystem permissions.'}, status_code=400)
    async def preset_endpoint(request):
        """DREAM-171: the Skills workspace's presets: GET the overview; POST {op, ..., sha256} to change them."""
        if not server._authorized(request):
            return JSONResponse({'error': 'unauthorized'}, status_code=401)
        origin = request.headers.get('origin')
        if request.method == 'POST' and origin and origin.rstrip('/') != str(request.base_url).rstrip('/'):
            return JSONResponse({'error': 'Origin does not match this session.'}, status_code=403)
        try:
            if request.method == 'POST':
                body = await request.body()
                if len(body) > 32_768:
                    return JSONResponse({'error': 'Preset request is too large.'}, status_code=413)
                payload = json.loads(body)
                if not isinstance(payload, dict):
                    raise ValueError('Expected a preset object.')
                if payload.get('op') == 'use':
                    await run_in_threadpool(presets.set_active, str(payload.get('preset')), payload.get('sha256'))
                else:
                    await run_in_threadpool(presets.edit, payload.get('op'), payload, payload.get('sha256'))
            result = await run_in_threadpool(_overview)
            return JSONResponse(result, headers={'Cache-Control': 'no-store'})
        except presets.Conflict as exc:
            return JSONResponse({'error': str(exc)}, status_code=409)
        except (ValueError, TypeError, KeyError, OSError) as exc:
            return JSONResponse({'error': str(exc)}, status_code=400)

    return [Route('/api/skills', endpoint, methods=['GET', 'POST']),
            Route('/api/skills/{name}', endpoint, methods=['GET', 'POST']),
            Route('/api/skills/{name}/remove', endpoint, methods=['POST']),
            Route('/api/presets', preset_endpoint, methods=['GET', 'POST'])]


def _overview():
    from ..skills import loader
    from ..tools.installed_skill_tools import inventory
    return presets.overview([s for s in inventory() if loader.enabled(s)])
