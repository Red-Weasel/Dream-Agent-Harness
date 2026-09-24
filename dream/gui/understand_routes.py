"""The Understand-Anything panel (owner request, DREAM-087): Dream serves the upstream dashboard and its data.

The dashboard (Egonex-AI/Understand-Anything, MIT; built once with ``vite build --base=/ua/``) is served from its dist
under ``/ua/``. It fetches its data from ROOT paths with ``?token=`` -- ``/knowledge-graph.json`` and the six others
below are what upstream's App.tsx, TokenGate.tsx and CodeViewer.tsx hard-code -- so those endpoints live at Dream's root,
behind Dream's session token, and read the project's ``.ua/`` folder (or the legacy ``.understand-anything/``). The
checks mirror upstream's viewer (packages/viewer/bin/viewer.mjs): node paths are relativised before leaving the machine,
and ``/file-content.json`` serves only files the graph names, inside the project, under 1 MB, never binary.
"""
from __future__ import annotations

import codecs
import hashlib
import json
import os
import re
import stat
import time
import subprocess
from datetime import datetime, timezone
from pathlib import Path

from starlette.concurrency import run_in_threadpool
from starlette.responses import FileResponse, HTMLResponse, JSONResponse
from starlette.routing import Route

UA_REPO = Path(os.environ.get("UA_DIR", str(Path.home() / ".understand-anything" / "repo"))).expanduser()
DASHBOARD = UA_REPO / "understand-anything-plugin" / "packages" / "dashboard"
DIST = DASHBOARD / "dist"
BUILD = f"cd {DASHBOARD} && npx vite build --base=/ua/"
DATA_DIRS = (".understand-anything", ".ua")     # legacy first, as upstream does
GRAPH_FILES = ("knowledge-graph.json", "domain-graph.json", "diff-overlay.json", "meta.json")
MAX_SOURCE_BYTES = 1024 * 1024
GIT_TIMEOUT = 5
GIT_EXCLUDES = ("--", ".", ":(exclude).understand-anything", ":(exclude).understand-anything/**",
                ":(exclude).ua", ":(exclude).ua/**")
LANGUAGES = {"bash": "bash", "c": "c", "cc": "cpp", "cpp": "cpp", "cs": "csharp", "css": "css", "go": "go", "h": "c",
             "hpp": "cpp", "html": "markup", "java": "java", "js": "javascript", "jsx": "jsx", "json": "json",
             "md": "markdown", "mjs": "javascript", "py": "python", "rb": "ruby", "rs": "rust", "sh": "bash",
             "ts": "typescript", "tsx": "tsx", "txt": "text", "yaml": "yaml", "yml": "yaml"}


def data_dir(project: Path) -> Path:
    for name in DATA_DIRS:
        if (project / name / "knowledge-graph.json").is_file():
            return project / name
    return project / ".ua"


def relative_path(file_path: str, project: Path) -> str | None:
    """A node's path as the browser may see it: relative to the project; a foreign absolute path is its basename."""
    if os.path.isabs(file_path):
        try:
            rel = Path(file_path).relative_to(project)
        except ValueError:
            return Path(file_path).name
    else:
        rel = Path(file_path)
    normalized = os.path.normpath(str(rel))
    if normalized in (".", "..") or normalized.startswith("../") or os.path.isabs(normalized) or "\0" in normalized:
        return None
    return normalized.replace(os.sep, "/")


def load_graph(project: Path, name: str = "knowledge-graph.json") -> dict:
    graph = json.loads((data_dir(project) / name).read_text("utf-8"))
    if isinstance(graph.get("nodes"), list):
        graph["nodes"] = [{**n, "filePath": relative_path(n["filePath"], project) or Path(n["filePath"]).name}
                          if isinstance(n, dict) and isinstance(n.get("filePath"), str) else n for n in graph["nodes"]]
    return graph


def status(project: Path | None) -> dict:
    out = {"dashboard": (DIST / "index.html").is_file(), "build": BUILD, "graph": None}
    if project is None:
        return out
    path = data_dir(project) / "knowledge-graph.json"
    if path.is_file():
        try:
            graph = json.loads(path.read_text("utf-8"))
            out["graph"] = {"nodes": len(graph.get("nodes") or []), "edges": len(graph.get("edges") or []),
                            "name": (graph.get("project") or {}).get("name"),
                            "analyzedAt": (graph.get("project") or {}).get("analyzedAt"), "path": str(path)}
        except (OSError, ValueError) as exc:
            out["graph_error"] = f"{type(exc).__name__}: {exc}"
    return out


def file_content(project: Path, requested: str) -> tuple[int, dict]:
    if not requested:
        return 400, {"error": "Missing path"}
    if "\0" in requested:
        return 400, {"error": "Invalid path"}
    if os.path.isabs(requested):
        return 400, {"error": "Absolute paths are not allowed"}
    normalized = os.path.normpath(requested)
    if normalized in (".", "..") or normalized.startswith("../"):
        return 400, {"error": "Path must stay inside the project"}
    target = (project / normalized).resolve()
    try:
        rel = target.relative_to(project.resolve()).as_posix()
    except ValueError:
        return 400, {"error": "Path must stay inside the project"}
    try:
        allowed = {relative_path(n["filePath"], project) for n in load_graph(project)["nodes"]
                   if isinstance(n, dict) and isinstance(n.get("filePath"), str)}
    except (OSError, ValueError, KeyError):
        allowed = set()
    if rel not in allowed:
        return 404, {"error": "File is not in the knowledge graph"}
    if not target.is_file():
        return 404, {"error": "File not found"}
    if target.stat().st_size > MAX_SOURCE_BYTES:
        return 413, {"error": "File is too large to preview"}
    data = target.read_bytes()
    if b"\0" in data:
        return 415, {"error": "Binary files cannot be previewed"}
    text = data.decode("utf-8", errors="replace")
    return 200, {"path": rel, "language": LANGUAGES.get(target.suffix[1:].lower(), "text"), "content": text,
                 "sizeBytes": len(data), "lineCount": 0 if not text else len(text.splitlines()) + (1 if text.endswith(("\n", "\r")) else 0)}


class _GitTimeout(Exception):
    pass


def _git(project: Path, *args: str) -> str | None:
    try:
        run = subprocess.run(["git", *args], cwd=project, capture_output=True, text=True, timeout=GIT_TIMEOUT)
    except subprocess.TimeoutExpired:
        raise _GitTimeout()
    except OSError:
        return None
    return run.stdout if run.returncode == 0 else None


def freshness(project: Path, graph: dict) -> dict:
    """upstream's GraphFreshnessResult (packages/core/src/staleness.ts), computed with git here."""
    meta = graph.get("project") or {}
    commit, analyzed = meta.get("gitCommitHash"), meta.get("analyzedAt")
    when = {"lastAnalyzedAt": analyzed} if isinstance(analyzed, str) else {}
    if not isinstance(commit, str) or not commit:
        return {"status": "unknown", "reason": "missing-graph-commit", **when}
    try:
        head = (_git(project, "rev-parse", "HEAD") or "").strip()
        if not head:
            return {"status": "unknown", "reason": "git-head-unavailable", "graphCommitHash": commit, **when}
        if _git(project, "cat-file", "-e", f"{commit}^{{commit}}") is None:
            return {"status": "unknown", "reason": "graph-commit-unavailable", "graphCommitHash": commit,
                    "headCommitHash": head, **when}
        porcelain = _git(project, "status", "--porcelain", *GIT_EXCLUDES) or ""
        dirty = sorted({line[3:].split(" -> ")[-1] for line in porcelain.splitlines() if len(line) > 3})
        if commit == head:
            return {"status": "dirty" if dirty else "fresh", "graphCommitHash": commit, "headCommitHash": head,
                    "changedFileCount": len(dirty), "changedFiles": dirty, "commitsBehind": 0, "commitsAhead": 0, **when}
        counts = (_git(project, "rev-list", "--left-right", "--count", f"{commit}...{head}") or "0\t0").split()
        ahead, behind = int(counts[0]), int(counts[1])    # left: only in the graph's commit; right: only in HEAD
        committed = (_git(project, "diff", "--name-only", commit, head, *GIT_EXCLUDES) or "").splitlines()
        changed = sorted(set(committed) | set(dirty))
    except _GitTimeout:
        return {"status": "unknown", "reason": "git-command-timeout", "graphCommitHash": commit, **when}
    return {"status": "stale", "relation": "behind" if not ahead else "ahead" if not behind else "diverged",
            "graphCommitHash": commit, "headCommitHash": head, "changedFileCount": len(changed), "changedFiles": changed,
            "commitsBehind": behind, "commitsAhead": ahead, **when}


_HASHES: dict[tuple[str, int, int, str], str] = {}     # (path, mtime_ns, size, method) -> sha256: a poll rehashes nothing unchanged


def _content_hash(path: Path, method: str) -> str | None:
    """SHA-256 of a project file as the understand skill records it: its bytes ('bytes', .ua/map-state.json) or its
    decoded UTF-8 text ('text', the plugin's fingerprints.json) -- read in 1 MB chunks, so a large binary a map
    recorded never sits in memory whole."""
    try:
        stat = path.stat()
        key = (str(path), stat.st_mtime_ns, stat.st_size, method)
        if key not in _HASHES:
            digest = hashlib.sha256()
            decoder = codecs.getincrementaldecoder("utf-8")("replace") if method == "text" else None
            with path.open("rb") as handle:
                for chunk in iter(lambda: handle.read(1 << 20), b""):
                    digest.update(decoder.decode(chunk).encode("utf-8") if decoder else chunk)
            if decoder:
                digest.update(decoder.decode(b"", final=True).encode("utf-8"))
            _HASHES[key] = digest.hexdigest()
        return _HASHES[key]
    except (OSError, ValueError):
        return None


def _epoch(when) -> float | None:
    """An ISO timestamp WITH a timezone (the glue's `...Z`, the plugin's toISOString); a naive one is not trusted."""
    try:
        moment = datetime.fromisoformat(str(when).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    return moment.timestamp() if moment.tzinfo is not None else None


def _run_epoch(run) -> float | None:
    """When the run began: the glue's run id starts with the UTC time `status` stamped BEFORE the scan took the hashes
    (`20260924T122212.123456Z-<pid>`)."""
    match = re.match(r"(\d{8}T\d{6})(?:\.(\d{1,6}))?Z", str(run or ""))
    if not match:
        return None
    try:
        moment = datetime.strptime(match.group(1), "%Y%m%dT%H%M%S").replace(tzinfo=timezone.utc)
    except ValueError:                                   # impossible digits (a hand-edited record): trust nothing
        return None
    return moment.timestamp() + float("0." + (match.group(2) or "0"))


def recorded_hashes(folder: Path) -> tuple[dict, str, str | None, float | None, str | None] | None:
    """(file -> sha256, hash method, when built, the moment no mapped file changed before, the map's git commit) that
    the understand skill recorded: DREAM-106's .ua/map-state.json -- its hashes are taken at the scan, so the moment is
    the run's start -- else a map from before it: the plugin's fingerprints.json (text hashes, built after
    lastAnalyzedAt). None without either."""
    def load(name: str) -> dict:
        try:
            data = json.loads((folder / name).read_text("utf-8"))
        except (OSError, ValueError):
            return {}
        return data if isinstance(data, dict) else {}

    state = load("map-state.json")
    if isinstance(state.get("files"), dict) and state["files"]:
        since = _run_epoch(state.get("run")) if state.get("run") else _epoch(state.get("builtAt"))
        return (state["files"], str(state.get("hash") or "bytes"), state.get("builtAt"), since,
                state.get("gitCommitHash") if isinstance(state.get("gitCommitHash"), str) else None)
    prints = load("fingerprints.json").get("files")
    if isinstance(prints, dict) and prints:
        files = {p: (f or {}).get("contentHash") if isinstance(f, dict) else None for p, f in prints.items()}
        meta = load("meta.json")
        built = meta.get("lastAnalyzedAt")
        return files, "text", built, _epoch(built), meta.get("gitCommitHash") if isinstance(meta.get("gitCommitHash"), str) else None
    return None


def content_freshness(project: Path, recorded: tuple) -> dict:
    """DREAM-107: freshness from the recorded file hashes, no git needed -- fresh while every mapped file is unchanged,
    dirty naming the edited and deleted ones (the skill's `status` names new files too, when the map is next updated).
    Only files modified since the record's moment are hashed (the run's start; a missing, naive or future moment hashes
    all); a recorded path that leaves the project, or is not a regular file, is never read and counts as changed."""
    files, method, built, since, commit = recorded
    if since is not None and since > time.time():
        since = None
    base = project.resolve()
    changed = []
    for rel, digest in files.items():
        try:
            path = (project / str(rel)).resolve()
            info = path.stat() if path.is_relative_to(base) else None
        except (OSError, ValueError, RuntimeError):
            info = None
        if info is None or not stat.S_ISREG(info.st_mode):
            changed.append(str(rel))                             # deleted, outside the project, or not a regular file
            continue
        if since is not None and info.st_mtime < since:
            continue                                             # untouched since the hashes were taken: not even read
        if not isinstance(digest, str) or _content_hash(path, method) != digest:
            changed.append(str(rel))
    changed.sort()
    when = {"lastAnalyzedAt": built} if isinstance(built, str) else {}
    # upstream's dashboard validates both commit fields as non-empty strings for every status (freshness.ts); without
    # git there is none, so the map's recorded commit, else "none"
    tag = commit if commit else "none"
    return {"status": "dirty" if changed else "fresh", "graphCommitHash": tag, "headCommitHash": tag,
            "changedFileCount": len(changed), "changedFiles": changed, "commitsBehind": 0, "commitsAhead": 0, **when}


def staleness(project: Path) -> tuple[int, dict]:
    folder = data_dir(project)
    if not (folder / "knowledge-graph.json").is_file():
        return 404, {"error": "No knowledge graph found. Run /understand first."}
    graphs = {}
    recorded = recorded_hashes(folder)   # the skill's own record answers without git, and matches its `status`
    for key, name in (("knowledge", "knowledge-graph.json"), ("domain", "domain-graph.json")):
        if (folder / name).is_file():
            try:
                graph = json.loads((folder / name).read_text("utf-8"))
                if recorded is not None and (folder / "map-state.json").is_file():
                    graphs[key] = content_freshness(project, recorded)          # content decides, as for `status`
                    continue
                result = freshness(project, graph)
                if recorded is not None and result.get("status") == "unknown":   # a map from before DREAM-106, no git
                    result = content_freshness(project, recorded)
                graphs[key] = result
            except (OSError, ValueError):
                return 500, {"error": "Failed to read graph file"}
    return 200, {"graphs": graphs}


def routes(server):
    def project():
        workspace = server._workspace()
        return Path(workspace).resolve() if workspace else None

    def unauthorized(request):
        return None if server._authorized(request) else JSONResponse({"error": "unauthorized"}, status_code=401)

    async def status_endpoint(request):
        if (denied := unauthorized(request)):
            return denied
        return JSONResponse(await run_in_threadpool(status, project()), headers={"Cache-Control": "no-store"})

    async def graph_endpoint(request):
        if (denied := unauthorized(request)):
            return denied
        root = project()
        if root is None:
            return JSONResponse({"error": "No workspace."}, status_code=404)
        name = request.url.path.lstrip("/")
        if name == "config.json":
            path = data_dir(root) / name
            if not path.is_file():
                return JSONResponse({"autoUpdate": False, "outputLanguage": "en"})
            try:
                return JSONResponse(json.loads(await run_in_threadpool(path.read_text, "utf-8")))
            except (OSError, ValueError):
                return JSONResponse({"error": "Failed to read config file"}, status_code=500)
        if name == "file-content.json":
            code, body = await run_in_threadpool(file_content, root, request.query_params.get("path", ""))
            return JSONResponse(body, status_code=code)
        if name == "staleness.json":
            code, body = await run_in_threadpool(staleness, root)
            return JSONResponse(body, status_code=code, headers={"Cache-Control": "no-store"})
        if not (data_dir(root) / name).is_file():
            if name == "knowledge-graph.json":
                return JSONResponse({"error": "No knowledge graph found. Run /understand first."}, status_code=404)
            return JSONResponse({"error": "Not found."}, status_code=404)
        try:
            return JSONResponse(await run_in_threadpool(load_graph, root, name), headers={"Cache-Control": "no-store"})
        except (OSError, ValueError):
            return JSONResponse({"error": "Failed to read graph file"}, status_code=500)

    async def dashboard(request):
        """The built dashboard; the bundle is trusted local software, served without a token like /assets."""
        dist = DIST.resolve()
        if not (dist / "index.html").is_file():
            return HTMLResponse(f"<h1>Understand-Anything dashboard not built</h1><p>Run:</p><pre>{BUILD}</pre>"
                                "<p>Then reload this panel.</p>", status_code=503)
        rel = request.path_params.get("path") or "index.html"
        target = (dist / rel).resolve()
        if not target.is_relative_to(dist) or not target.is_file():
            return HTMLResponse("Not found", status_code=404)
        return FileResponse(target)

    return [Route("/api/understand/status", status_endpoint, methods=["GET"]),
            *[Route(f"/{name}", graph_endpoint, methods=["GET"])
              for name in (*GRAPH_FILES, "config.json", "file-content.json", "staleness.json")],
            Route("/ua/", dashboard, methods=["GET"]), Route("/ua/{path:path}", dashboard, methods=["GET"])]
