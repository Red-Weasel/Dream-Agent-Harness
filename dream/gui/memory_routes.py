"""The Memory page (owner request, DREAM-083): read, edit and delete what Dream
remembers -- global long-term memories (memory/<name>.md) and project notebooks
(memory/projects/<project>/PROJECT.md). Authenticated like the skill editor; a
save or delete touches only files inside Dream's memory folder."""
import json
import re
import shutil
from datetime import datetime
from pathlib import Path

from starlette.concurrency import run_in_threadpool
from starlette.responses import JSONResponse
from starlette.routing import Route

from .. import config
from ..memory import longterm, project as project_memory

MAX_JSON = 768_000
_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,120}$")


def _stamp(path: Path) -> str:
    return datetime.fromtimestamp(path.stat().st_mtime).isoformat(timespec="minutes")


def listing() -> dict:
    items = []
    for f in longterm.memory_files():
        mem = longterm.read_file(f)
        items.append({"scope": "global", "name": f.stem, "title": (mem or {}).get("title") or f.stem,
                      "description": (mem or {}).get("description", ""), "path": str(f),
                      "size": f.stat().st_size, "updated": _stamp(f)})
    root = project_memory.projects_dir()
    for d in sorted(p for p in root.glob("*") if p.is_dir()) if root.is_dir() else []:
        nb = d / project_memory.NOTEBOOK
        if not nb.is_file():
            continue
        marker = d / ".workspace"
        workspace = marker.read_text(encoding="utf-8").strip() if marker.is_file() else ""
        items.append({"scope": "project", "name": d.name,
                      "title": (Path(workspace).name if workspace else d.name) + " — project notebook",
                      "description": workspace, "path": str(nb), "size": nb.stat().st_size, "updated": _stamp(nb)})
    return {"items": items, "memory_dir": str(config.MEMORY_DIR)}


def _path(scope: str, name: str) -> Path:
    if not _NAME.match(name or "") or scope not in ("global", "project"):
        raise ValueError("Unknown memory.")
    path = (config.MEMORY_DIR / f"{name}.md" if scope == "global"
            else project_memory.projects_dir() / name / project_memory.NOTEBOOK)
    if not path.resolve().is_relative_to(config.MEMORY_DIR.resolve()):
        raise ValueError("Unknown memory.")
    if scope == "global" and longterm.is_reserved(path.name):
        raise ValueError("That file is not a memory.")
    return path


def read(scope: str, name: str) -> dict:
    path = _path(scope, name)
    if not path.is_file():
        raise FileNotFoundError(name)
    return {"scope": scope, "name": name, "path": str(path), "text": path.read_text(encoding="utf-8", errors="replace")}


def save(scope: str, name: str, payload: dict) -> dict:
    path = _path(scope, name)
    if not path.is_file():
        raise FileNotFoundError(name)
    if payload.get("delete") is True:
        if scope == "global":
            longterm.delete_markdown(name)
        else:
            shutil.rmtree(path.parent)
        return {"deleted": True, "scope": scope, "name": name}
    text = payload.get("text")
    if not isinstance(text, str) or not text.strip():
        raise ValueError("Memory text must not be empty; use Delete to remove it.")
    path.write_text(text, encoding="utf-8")
    if scope == "global":
        longterm.write_index()
    return {"saved": True, "scope": scope, "name": name, "path": str(path)}


def routes(server):
    async def endpoint(request):
        if not server._authorized(request):
            return JSONResponse({'error': 'unauthorized'}, status_code=401)
        origin = request.headers.get('origin')
        if request.method == 'POST' and origin and origin.rstrip('/') != str(request.base_url).rstrip('/'):
            return JSONResponse({'error': 'Origin does not match this session.'}, status_code=403)
        try:
            scope, name = request.path_params.get('scope'), request.path_params.get('name')
            if request.method == 'GET':
                result = await run_in_threadpool(read, scope, name) if name else await run_in_threadpool(listing)
            else:
                body = await request.body()
                if len(body) > MAX_JSON:
                    return JSONResponse({'error': 'Memory request is too large.'}, status_code=413)
                payload = json.loads(body)
                if not isinstance(payload, dict):
                    raise ValueError('Expected a memory object.')
                result = await run_in_threadpool(save, scope, name, payload)
            return JSONResponse(result, headers={'Cache-Control': 'no-store'})
        except FileNotFoundError:
            return JSONResponse({'error': 'That memory no longer exists.'}, status_code=404)
        except (ValueError, TypeError, json.JSONDecodeError) as exc:
            return JSONResponse({'error': str(exc)}, status_code=400)
        except OSError:
            return JSONResponse({'error': 'Cannot safely access the memory files.'}, status_code=400)
    return [Route('/api/memory', endpoint, methods=['GET']),
            Route('/api/memory/{scope}/{name}', endpoint, methods=['GET', 'POST'])]
