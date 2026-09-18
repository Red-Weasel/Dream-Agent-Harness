"""Studio's async-JavaScript worker, including its Python file bindings, is contained.

No preview browser or host file callback executes model code. Only the installed
Playwright runtime and headless-shell distribution are added as read-only mounts.
The worker uses CPU canvas; no GPU devices are exposed by the executor.
"""
from __future__ import annotations

import asyncio
import base64
import json
import os
import sysconfig
from dataclasses import replace
from pathlib import Path
from typing import Mapping, Sequence

from .execution import ExecutionContext, ExecutionRefused, ExecutionUnavailable, _SUPERVISOR_PYTHON, run_contained

SCRIPT_TIMEOUT = 30.0
MAX_CAPTURE_BYTES = 32 * 1024 * 1024
MAX_CODE_BYTES = 1024 * 1024
_SITE = Path(sysconfig.get_path("purelib")).resolve()
_BROWSERS = Path(os.environ.get("PLAYWRIGHT_BROWSERS_PATH") or
                 Path.home() / ".cache/ms-playwright").expanduser().resolve()


class ScriptTimeout(asyncio.TimeoutError):
    def __init__(self, logs: list[str]):
        super().__init__("contained script timed out; owned processes terminated and reaped")
        self.logs = logs


def _runtime() -> tuple[Path, tuple[Path, ...]]:
    manifest = _SITE / "playwright/driver/package/browsers.json"
    try:
        browsers = json.loads(manifest.read_text())["browsers"]
        revision = next(b["revision"] for b in browsers if b["name"] == "chromium-headless-shell")
        if not str(revision).isdigit():
            raise ValueError("invalid headless-shell revision")
        root = _BROWSERS / f"chromium_headless_shell-{revision}"
        executable = next((p for pattern in ("*/chrome-headless-shell", "*/headless_shell")
                           for p in root.glob(pattern) if p.is_file()), None)
        if executable is None:
            raise ValueError("installed Playwright headless shell was not found")
        # Only these runtime packages are needed; avoid mounting a whole home,
        # browser profile, venv, or application checkout for an unrelated project.
        packages = tuple(_SITE / p for p in ("playwright", "pyee", "greenlet"))
        if any(not p.is_dir() for p in packages):
            raise ValueError("installed Playwright Python dependencies are missing")
        return executable.resolve(), (*packages, executable.parent.resolve())
    except (OSError, KeyError, StopIteration, ValueError) as exc:
        raise ExecutionUnavailable(f"contained JavaScript runtime unavailable: {exc}") from exc


async def run_script(code: str, context: ExecutionContext,
                     captures: Mapping[str, Sequence[bytes]], *, timeout: float | None = None) -> list[str]:
    if not isinstance(code, str) or not code.strip():
        raise ExecutionRefused("run_script needs a non-empty JavaScript code string")
    if len(code.encode("utf-8")) > MAX_CODE_BYTES:
        raise ExecutionRefused("script exceeds the 1 MiB code limit")
    context.scope.validate()
    if context.mode == "plan":
        raise ExecutionRefused("plan mode — no scripts")
    size = sum(len(blob) for blobs in captures.values() for blob in blobs)
    if size > MAX_CAPTURE_BYTES:
        raise ExecutionRefused("capture inputs exceed the 32 MiB worker limit")
    executable, roots = _runtime()
    scope = replace(context.scope, read_roots=tuple(dict.fromkeys((*context.scope.read_roots, *roots))))
    payload = json.dumps({"code": code, "workspace": str(scope.workspace), "site": str(_SITE),
                          "browser": str(executable), "captures": {
                              key: [base64.b64encode(blob).decode("ascii") for blob in blobs]
                              for key, blobs in captures.items()}}).encode("utf-8")
    result = await run_contained([_SUPERVISOR_PYTHON, "-I", "-c", _WORKER], scope,
                                 timeout=SCRIPT_TIMEOUT if timeout is None else timeout,
                                 input_data=payload)
    logs, errors, completed = [], [], False
    for line in result.output.decode("utf-8", "replace").splitlines():
        try:
            event = json.loads(line)
        except ValueError:
            continue
        if not isinstance(event, dict):
            continue
        if event.get("kind") == "log":
            logs.append(str(event.get("text", "")))
        elif event.get("kind") == "error":
            errors.append(str(event.get("text", "script failed")))
        elif event.get("kind") == "done":
            completed = True
    if result.timed_out:
        raise ScriptTimeout(logs)
    if result.returncode != 0 or errors or not completed:
        detail = errors[0] if errors else result.output.decode("utf-8", "replace")[-2000:]
        raise RuntimeError(detail or f"contained worker exited {result.returncode} without completion")
    if result.truncated:
        logs.append("[… script output truncated]")
    return logs


# Passed from already-loaded harness memory. Project edits cannot replace a
# future worker bootstrap on the host. Every callback below runs inside bwrap.
_WORKER = r'''
import asyncio, base64, json, sys
from pathlib import Path
data = json.loads(sys.stdin.buffer.read())
sys.path.insert(0, data["site"])
from playwright.async_api import async_playwright
workspace = Path(data["workspace"])
logged = 0

def emit(kind, text=""):
    print(json.dumps({"kind": kind, "text": str(text)[:16000]}), flush=True)

def inside(raw):
    path = (workspace / str(raw)).resolve()
    if not path.is_relative_to(workspace):
        raise ValueError(str(raw) + ": outside the workspace")
    return path

async def read_text(source, raw):
    return inside(raw).read_text(encoding="utf-8", errors="replace")

async def read_binary(source, raw):
    return base64.b64encode(inside(raw).read_bytes()).decode("ascii")

async def save(source, raw, value, binary):
    path = inside(raw)
    path.parent.mkdir(parents=True, exist_ok=True)
    if binary:
        path.write_bytes(base64.b64decode(value))
    else:
        path.write_text(value, encoding="utf-8")
    return str(path.relative_to(workspace))

async def listing(source, raw):
    return sorted(p.name + ("/" if p.is_dir() else "") for p in inside(raw or ".").iterdir())

async def captures(source, key):
    return data["captures"].get(str(key), [])

async def log(source, text):
    global logged
    if logged < 2000:
        emit("log", text)
        logged += 1

async def main():
    async with async_playwright() as pw:
        browser = await pw.chromium.launch(executable_path=data["browser"], headless=True,
            args=["--disable-gpu", "--disable-software-rasterizer", "--disable-dev-shm-usage"])
        context = await browser.new_context(service_workers="block")
        async def route(request):
            if request.request.url.startswith(("data:", "blob:", "about:")):
                await request.continue_()
            else:
                await request.abort()
        await context.route("**/*", route)
        page = await context.new_page()
        for name, callback in (("__readFile", read_text), ("__readFileB64", read_binary),
                ("__saveFile", save), ("__ls", listing), ("__captures", captures), ("__log", log)):
            await page.expose_binding(name, callback)
        await page.set_content("<!doctype html><title>run_script</title>")
        helpers = r"""
        const log = (...a) => window.__log(a.map(x => typeof x === 'string' ? x : JSON.stringify(x)).join(' '));
        const readFile = p => window.__readFile(String(p));
        const b64ToBlob = (b64, type) => { const s = atob(b64); const u = new Uint8Array(s.length);
          for (let i = 0; i < s.length; i++) u[i] = s.charCodeAt(i); return new Blob([u], {type: type || 'application/octet-stream'}); };
        const readFileBinary = async p => b64ToBlob(await window.__readFileB64(String(p)));
        const readImage = async p => { const blob = await readFileBinary(p); const url = URL.createObjectURL(blob);
          const img = new Image(); await new Promise((ok, no) => { img.onload = ok; img.onerror = () => no(new Error('not an image: ' + p)); img.src = url; }); return img; };
        const blobToB64 = blob => new Promise((ok, no) => { const r = new FileReader(); r.onload = () => ok(String(r.result).split(',')[1]); r.onerror = no; r.readAsDataURL(blob); });
        const saveFile = async (p, data) => {
          if (typeof data === 'string') return window.__saveFile(String(p), data, false);
          if (data && data.tagName === 'CANVAS') return window.__saveFile(String(p), data.toDataURL('image/png').split(',')[1], true);
          if (data instanceof Blob) return window.__saveFile(String(p), await blobToB64(data), true);
          throw new Error('saveFile: data must be a string, a Canvas, or a Blob');
        };
        const ls = p => window.__ls(p === undefined ? '' : String(p));
        const getCaptures = async key => (await window.__captures(String(key))).map(b => b64ToBlob(b, 'image/png'));
        const createCanvas = (w, h) => { const c = document.createElement('canvas'); c.width = w; c.height = h; return c; };
        """
        await page.evaluate("(async () => {" + helpers + "\n" + data["code"] + "\n})()")
        await browser.close()
    emit("done")

try:
    asyncio.run(main())
except Exception as error:
    emit("error", type(error).__name__ + ": " + str(error))
    raise SystemExit(1)
'''
