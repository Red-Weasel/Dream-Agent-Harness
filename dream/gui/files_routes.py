"""The Files page (owner request, DREAM-084): browse the project folder and Dream's own
setup folders as expandable trees, and read a file. Read-only and token-authenticated;
every path is resolved and must stay inside its root (a symlink cannot lead out)."""
from pathlib import Path

from starlette.concurrency import run_in_threadpool
from starlette.responses import JSONResponse
from starlette.routing import Route

from .. import config

HIDDEN = {".git", "node_modules", ".venv", "__pycache__", ".pytest_cache", ".mypy_cache", ".ruff_cache"}
DREAM_ENTRIES = ("skills", "memory")     # Dream's setup folders the owner edits; runtime state stays out
MAX_READ = 400_000
MAX_ENTRIES = 2000


def _roots(server) -> dict[str, Path]:
    roots = {"dream": config.ROOT.resolve()}
    workspace = server._workspace()
    if workspace:
        roots["workspace"] = Path(workspace).resolve()
    return roots


def _resolve(roots: dict[str, Path], root: str, rel: str) -> tuple[Path, Path]:
    base = roots.get(root)
    if base is None:
        raise ValueError("Unknown folder.")
    rel = (rel or "").strip("/")
    parts = [p for p in rel.split("/") if p]
    if any(p in ("..", ".") for p in parts):
        raise ValueError("Unknown path.")
    if root == "dream" and parts and parts[0] not in DREAM_ENTRIES:
        raise ValueError("Only Dream's setup folders are browsable here.")
    target = base.joinpath(*parts).resolve()
    if not target.is_relative_to(base):
        raise ValueError("That path leads outside the folder.")
    return base, target


def listing(roots: dict[str, Path], root: str, rel: str) -> dict:
    base, target = _resolve(roots, root, rel)
    if not target.is_dir():
        raise FileNotFoundError(rel)
    entries = []
    if root == "dream" and target == base:
        children = [base / name for name in DREAM_ENTRIES if (base / name).is_dir()]
    else:
        children = list(target.iterdir())
    for child in children:
        if child.name in HIDDEN or (child.name.startswith(".") and child.name not in (".dream",)):
            continue
        try:
            is_dir = child.is_dir()
            if not child.resolve().is_relative_to(base):
                continue      # a symlink that points out of the folder is not shown
            entries.append({"name": child.name, "type": "dir" if is_dir else "file",
                            "size": 0 if is_dir else child.stat().st_size})
        except OSError:
            continue
        if len(entries) >= MAX_ENTRIES:
            break
    entries.sort(key=lambda e: (e["type"] != "dir", e["name"].lower()))
    return {"root": root, "path": str(target.relative_to(base)) if target != base else "",
            "base": str(base), "entries": entries, "truncated": len(entries) >= MAX_ENTRIES}


def read(roots: dict[str, Path], root: str, rel: str) -> dict:
    base, target = _resolve(roots, root, rel)
    if not target.is_file():
        raise FileNotFoundError(rel)
    data = target.read_bytes()[:MAX_READ + 1]
    if b"\x00" in data[:8192]:
        return {"root": root, "path": rel, "binary": True, "size": target.stat().st_size, "text": ""}
    return {"root": root, "path": rel, "binary": False, "size": target.stat().st_size,
            "truncated": len(data) > MAX_READ, "text": data[:MAX_READ].decode("utf-8", errors="replace")}


def routes(server):
    async def endpoint(request):
        if not server._authorized(request):
            return JSONResponse({'error': 'unauthorized'}, status_code=401)
        root = request.query_params.get('root', 'workspace')
        rel = request.query_params.get('path', '')
        try:
            roots = _roots(server)
            fn = read if request.url.path.endswith('/read') else listing
            result = await run_in_threadpool(fn, roots, root, rel)
            return JSONResponse(result, headers={'Cache-Control': 'no-store'})
        except FileNotFoundError:
            return JSONResponse({'error': 'Not found.'}, status_code=404)
        except ValueError as exc:
            return JSONResponse({'error': str(exc)}, status_code=400)
        except OSError:
            return JSONResponse({'error': 'Cannot read that path.'}, status_code=400)
    return [Route('/api/files', endpoint, methods=['GET']), Route('/api/files/read', endpoint, methods=['GET'])]
