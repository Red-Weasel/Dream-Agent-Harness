"""Sleepwalk's authenticated endpoints (DREAM-156): list, save, delete, Run now, and the run records."""
from __future__ import annotations

import json

from starlette.concurrency import run_in_threadpool
from starlette.responses import JSONResponse
from starlette.routing import Route

from ..sleepwalk import background, connectors, runner, schedule, store

MAX_JSON = 64 * 1024
PREFERRED = ("codex", "xai", "anthropic")    # Codex, the Grok API, Claude: runners that can be isolated


def default_runner(choices: list[dict]) -> dict | None:
    """The first available of Codex, the Grok API, Claude, with its first listed model and effort 'high' when offered."""
    row = next((c for key in PREFERRED for c in choices if c["key"] == key and c.get("available")), None)
    if row is None:
        return None
    model = next(iter(row.get("models") or []), None)
    efforts = model["efforts"] if model else row.get("efforts") or []
    return {"provider": row["key"], "model": model["id"] if model else None,
            "effort": "high" if "high" in efforts else None}


def _choices() -> list[dict]:
    from ..core import council_config, moe
    return [{**{k: row.get(k) for k in ("key", "label", "available", "note", "efforts", "models")},
             "isolated": moe.can_isolate(row["key"])} for row in council_config.provider_choices()]


def _files(identifier: str) -> list[str]:
    folder = store.attachments(identifier)
    return sorted(p.name for p in folder.iterdir()) if folder.is_dir() else []


def routes(server):
    def refused(request):
        if not server._authorized(request):
            return JSONResponse({"error": "unauthorized"}, status_code=401)
        origin = request.headers.get("origin")
        if request.method == "POST" and origin and origin.rstrip("/") != str(request.base_url).rstrip("/"):
            return JSONResponse({"error": "origin does not match this Studio session"}, status_code=403)

    async def body(request) -> dict:
        data = bytearray()
        async for chunk in request.stream():
            data.extend(chunk)
            if len(data) > MAX_JSON:
                raise ValueError("Request exceeds 64 KiB")
        payload = json.loads(data)
        if not isinstance(payload, dict):
            raise ValueError("Expected an object")
        return payload

    def answer(fn):
        async def endpoint(request):
            if (denied := refused(request)) is not None:
                return denied
            try:
                return JSONResponse(await fn(request), headers={"Cache-Control": "no-store"})
            except KeyError:
                return JSONResponse({"error": "No such automation or run"}, status_code=404)
            except (ValueError, TypeError, OSError) as exc:
                return JSONResponse({"error": str(exc)}, status_code=400)
        return endpoint

    async def overview(request):
        choices, problems, now = await run_in_threadpool(_choices), [], schedule.now()
        automations = [{**a, "next": schedule.next_run(a, now) if a["enabled"] else None, "attachments": _files(a["id"])}
                       for a in await run_in_threadpool(store.load, problems)]
        found = await run_in_threadpool(connectors.discover, problems)
        return {"automations": automations, "problems": problems, "background": await run_in_threadpool(background.state),
                "linger": await run_in_threadpool(background.linger),
                "connectors": await run_in_threadpool(connectors.describe, found),
                "templates": store.templates(), "runners": choices,
                "default_runner": default_runner([c for c in choices if c["isolated"]]), "icons": store.ICONS}

    async def save(request):
        return {"automation": await run_in_threadpool(store.save, (await body(request)).get("automation"))}

    async def delete(request):
        await run_in_threadpool(store.delete, (await body(request)).get("id"))
        return {"deleted": True}

    async def switch(request):
        on = (await body(request)).get("on")
        if not isinstance(on, bool):
            raise ValueError("Expected on: true or false")
        return {"background": await run_in_threadpool(background.turn_on if on else background.turn_off)}

    async def connector(request):
        payload = await body(request)
        found = await run_in_threadpool(connectors.discover)
        await run_in_threadpool(connectors.save, found, payload.get("id"), payload.get("values"))
        return {"connectors": await run_in_threadpool(connectors.describe, found)}

    async def attach(request):
        from .uploads import receive_file              # its UploadError is a ValueError: a 400 here
        name, data = await receive_file(request)
        identifier = request.query_params.get("id", "")
        await run_in_threadpool(store.attach, identifier, name, data)
        return {"attachments": _files(identifier)}

    async def detach(request):
        payload = await body(request)
        await run_in_threadpool(store.detach, payload.get("id"), payload.get("name"))
        return {"attachments": _files(payload.get("id"))}

    async def run_now(request):
        automation = await run_in_threadpool(store.get, (await body(request)).get("id"))
        return {"run": await runner.run(automation)}

    async def runs(request):
        problems = []
        return {"runs": await run_in_threadpool(store.runs, 100, problems), "problems": problems}

    async def one_run(request):
        p = request.path_params
        return {"run": await run_in_threadpool(store.read_run, p["automation"], p["run"])}

    return [Route("/api/sleepwalk", answer(overview)),
            Route("/api/sleepwalk/save", answer(save), methods=["POST"]),
            Route("/api/sleepwalk/delete", answer(delete), methods=["POST"]),
            Route("/api/sleepwalk/connectors", answer(connector), methods=["POST"]),
            Route("/api/sleepwalk/background", answer(switch), methods=["POST"]),
            Route("/api/sleepwalk/attach", answer(attach), methods=["POST"]),
            Route("/api/sleepwalk/detach", answer(detach), methods=["POST"]),
            Route("/api/sleepwalk/run", answer(run_now), methods=["POST"]),
            Route("/api/sleepwalk/runs", answer(runs)),
            Route("/api/sleepwalk/runs/{automation}/{run}", answer(one_run))]
