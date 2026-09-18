"""SearXNG client with best-effort autostart.

Uses the local SearXNG instance (JSON API already enabled in its settings). If it's
down and autostart is on, Dream tries to launch it from its source checkout using an
interpreter that can import ``searx``. Autostart is best-effort: if it can't, search
returns clear guidance and the agent can still ``browse`` direct URLs.
"""

from __future__ import annotations

import asyncio
import os
import shutil
import subprocess
import time
from pathlib import Path
from typing import Any

import anyio
import httpx

from .. import config


class SearxngUnavailable(RuntimeError):
    pass


def _pid_file() -> Path:
    return config.VAR_DIR / "searxng.pid"


def _log_file() -> Path:
    return config.LOG_DIR / "searxng.log"


async def is_up(timeout: float = 2.0) -> bool:
    async with httpx.AsyncClient(timeout=timeout) as client:
        for path in ("/healthz", "/"):
            try:
                r = await client.get(config.SEARXNG_URL + path)
                if r.status_code < 500:
                    return True
            except httpx.HTTPError:
                continue
    return False


def _candidate_pythons() -> list[str]:
    cands: list[str] = []
    if config.SEARXNG_PYTHON:
        cands.append(config.SEARXNG_PYTHON)
    # Common venv locations inside the searxng checkout / alongside it.
    for rel in ("searx-pyenv", ".venv", "venv"):
        for base in (config.SEARXNG_DIR, config.SEARXNG_DIR.parent):
            p = base / rel / "bin" / "python"
            if p.exists():
                cands.append(str(p))
    for name in ("python3.11", "python3", "python"):
        found = shutil.which(name)
        if found:
            cands.append(found)
    # De-dup, preserve order.
    seen: set[str] = set()
    return [c for c in cands if not (c in seen or seen.add(c))]


def _searx_python() -> str | None:
    """First interpreter that can import ``searx`` with the checkout on its path."""
    env = {**os.environ, "PYTHONPATH": str(config.SEARXNG_DIR)}
    for py in _candidate_pythons():
        try:
            r = subprocess.run(
                [py, "-c", "import searx"],
                env=env,
                capture_output=True,
                timeout=15,
            )
            if r.returncode == 0:
                return py
        except (subprocess.SubprocessError, OSError):
            continue
    return None


def _settings_path() -> str | None:
    for name in ("my_settings.yml", "searx/settings.yml"):
        p = config.SEARXNG_DIR / name
        if p.exists():
            return str(p)
    return None


def _spawn(py: str) -> subprocess.Popen:
    """Blocking part of the autostart; run off the event loop."""
    config.LOG_DIR.mkdir(parents=True, exist_ok=True)
    env = {
        **os.environ,
        "PYTHONPATH": str(config.SEARXNG_DIR),
        "SEARXNG_BIND_ADDRESS": config.SEARXNG_HOST,
        "SEARXNG_PORT": str(config.SEARXNG_PORT),
    }
    settings = _settings_path()
    if settings:
        env["SEARXNG_SETTINGS_PATH"] = settings
    with _log_file().open("a", encoding="utf-8") as log:
        proc = subprocess.Popen(
            [py, "-m", "searx.webapp"],
            cwd=str(config.SEARXNG_DIR),
            env=env,
            stdout=log,
            stderr=subprocess.STDOUT,
            start_new_session=True,
        )
    _pid_file().write_text(str(proc.pid), encoding="utf-8")
    return proc


_start_lock = asyncio.Lock()


async def ensure_up() -> bool:
    """Return True if SearXNG is reachable, starting it if needed and possible.

    The instance is deliberately left running when Dream exits — it's a local service,
    and a warm instance makes the next boot instant. The pid file records the one we
    started so a later boot reuses it instead of stacking a second copy.
    """
    if await is_up():
        return True
    if not config.SEARXNG_AUTOSTART or not config.SEARXNG_DIR.exists():
        return False

    async with _start_lock:
        if await is_up():  # someone else won the race while we waited
            return True
        # Interpreter probing and Popen are blocking — keep them off the loop.
        py = await anyio.to_thread.run_sync(_searx_python)
        if not py:
            return False
        proc = await anyio.to_thread.run_sync(_spawn, py)

        deadline = time.monotonic() + 25
        while time.monotonic() < deadline:
            if proc.poll() is not None:  # died on startup
                return False
            if await is_up():
                return True
            await asyncio.sleep(0.7)
    return False


async def search(
    query: str,
    categories: str = "general",
    limit: int = 8,
    language: str = "en",
    time_range: str | None = None,
) -> dict[str, Any]:
    """Run a search and return a normalized dict of results.

    Raises ``SearxngUnavailable`` if the instance can't be reached.
    """
    if not await ensure_up():
        raise SearxngUnavailable(
            f"SearXNG is not reachable at {config.SEARXNG_URL} and could not be "
            f"auto-started. Start it manually, e.g.:\n"
            f"  cd {config.SEARXNG_DIR} && python -m searx.webapp"
        )

    params: dict[str, str] = {
        "q": query,
        "format": "json",
        "categories": categories,
        "language": language,
    }
    if time_range:
        params["time_range"] = time_range

    async with httpx.AsyncClient(timeout=config.HTTP_TIMEOUT_S) as client:
        r = await client.get(config.SEARXNG_URL + "/search", params=params)
        r.raise_for_status()
        data = r.json()

    results = []
    for item in (data.get("results") or [])[:limit]:
        results.append(
            {
                "title": item.get("title", ""),
                "url": item.get("url", ""),
                "content": item.get("content", ""),
                "engine": item.get("engine", ""),
            }
        )
    return {
        "query": query,
        "results": results,
        "answers": data.get("answers") or [],
        "suggestions": (data.get("suggestions") or [])[:6],
        "number_of_results": data.get("number_of_results"),
    }
