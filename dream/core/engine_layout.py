"""Which model runs where on the local engine, read only (DREAM-144, settings design P3 phase S3).

`ie supervise --config <layout>` (the engine's docs/serve_config.md) fronts several `ie serve` children, one per
card set, and routes each request by its "model" field. Its front answers GET /v1/models (every server: `id` = the
server's name, `root` = the model file's id, `status`), GET /health (`status`, `default`, `servers`: state, model,
port, cards, pid, restarts) and GET /props?model=<name> (that child's own /props, `n_ctx` included). A plain
`ie serve` answers the same paths for its one model, without `servers` in /health. This module reads those and
gives one table -- server, model, cards, port, context, state, and which one Dream treats as main -- for
`dream engine layout`, the launcher and the Settings tab. It never starts, stops or configures anything; an
endpoint that does not answer is reported in the view, not raised. DREAM-151: each server's `lanes`, how many requests
it runs at once (its /props total_slots, `ie serve --parallel N`).
"""
from __future__ import annotations

import json
from pathlib import Path
from urllib.parse import quote

import httpx


def _get(url: str, timeout: float):
    """(status, parsed body) or None when nothing answers or the body is not JSON."""
    try:
        response = httpx.get(url, timeout=timeout)
        return response.status_code, response.json()
    except (httpx.HTTPError, ValueError):
        return None


def read_layout(path: Path) -> dict[str, dict]:
    """The layout file's servers by name (`cards`, `ctx`, `port`, `model` as written). ValueError names the file."""
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        servers = data["servers"] if isinstance(data, dict) else None
        if not isinstance(servers, list) or not all(isinstance(s, dict) and isinstance(s.get("name"), str)
                                                   for s in servers):
            raise ValueError("no servers array with named entries")
    except (OSError, ValueError, KeyError, TypeError) as exc:
        raise ValueError(f"layout file {path} could not be read: {exc}") from exc
    return {s["name"]: s for s in servers}


def _n_ctx(props) -> int | None:
    generation = props.get("default_generation_settings") if isinstance(props, dict) else None
    value = generation.get("n_ctx") if isinstance(generation, dict) else None
    return value if type(value) is int and value > 0 else None


def _lanes(value) -> int | None:
    """How many requests a server runs at once (DREAM-151): its /props `total_slots`, a layout file's `parallel`."""
    return value if type(value) is int and value >= 1 else None


def layout_view(base_url: str | None = None, *, layout_path: str | Path | None = None, timeout: float = 3.0) -> dict:
    """The table. `base_url` is the engine's root or its /v1 (default: Dream's MachX endpoint); `layout_path`
    fills `cards`, `ctx` and `port` of a server the front cannot describe yet (one still loading, or down).

    Keys: `endpoint`, `reachable`, `supervised`, `status` (the front's: ok, degraded, loading, unavailable,
    stopping; a plain server's /health status), `default` (the supervisor's), `main` (the model Dream attaches
    to, machx.choose_main's rule), `servers` (one dict each: name, model, cards, port, ctx, state, pid, restarts,
    lanes, in the front's order), `layout_file`, `notes` (what could not be read)."""
    from ..local import machx
    root = (base_url or machx.BASE_URL).removesuffix("/v1").rstrip("/")
    view = {"endpoint": root, "reachable": False, "supervised": False, "status": None, "default": None,
            "main": None, "servers": [], "layout_file": str(layout_path) if layout_path is not None else None,
            "notes": []}
    file_servers: dict[str, dict] = {}
    if layout_path is not None:
        try:
            file_servers = read_layout(Path(layout_path))
        except ValueError as exc:
            view["notes"].append(str(exc))

    listed = _get(f"{root}/v1/models", timeout)
    if listed is None or listed[0] != 200 or not isinstance(listed[1], dict):
        view["notes"].append(f"{root} did not answer /v1/models; is the engine running there?")
        return view
    data = listed[1].get("data")
    if not isinstance(data, list):
        view["notes"].append(f"{root} answered /v1/models without a model list")
        data = []
    models = [m for m in data if isinstance(m, dict) and isinstance(m.get("id"), str)]
    view["reachable"] = True

    health = _get(f"{root}/health", timeout)
    body = health[1] if health is not None and isinstance(health[1], dict) else {}
    servers = body.get("servers")
    view["status"] = body.get("status") if isinstance(body.get("status"), str) else None
    if isinstance(servers, dict):
        view["supervised"] = True
        view["default"] = body.get("default") if isinstance(body.get("default"), str) else None
        roots = {m["id"]: m.get("root") for m in models}
        for name, entry in servers.items():
            entry = entry if isinstance(entry, dict) else {}
            from_file = file_servers.get(name, {})
            state = entry.get("state") if isinstance(entry.get("state"), str) else "unknown"
            ctx = lanes = None
            if state == "ready":
                props = _get(f"{root}/props?model={quote(name, safe='')}", timeout)
                answer = props[1] if props is not None and props[0] == 200 and isinstance(props[1], dict) else {}
                ctx, lanes = _n_ctx(answer), _lanes(answer.get("total_slots"))
                if ctx is None:
                    view["notes"].append(f"{name}: its /props did not report a context size")
            if ctx is None and type(from_file.get("ctx")) is int:
                ctx = from_file["ctx"]
            if lanes is None:
                lanes = _lanes(from_file.get("parallel"))
            cards = entry.get("cards") if isinstance(entry.get("cards"), list) else from_file.get("cards")
            port = entry.get("port") if type(entry.get("port")) is int else from_file.get("port")
            model = entry.get("model") if isinstance(entry.get("model"), str) else roots.get(name)
            view["servers"].append({
                "name": name, "model": model, "cards": cards if isinstance(cards, list) else None,
                "port": port if type(port) is int else None, "ctx": ctx, "state": state,
                "pid": entry.get("pid") if type(entry.get("pid")) is int else None,
                "restarts": entry.get("restarts") if type(entry.get("restarts")) is int else None, "lanes": lanes})
    else:
        if health is None:
            view["notes"].append(f"{root} did not answer /health")
        props = _get(f"{root}/props", timeout)
        answer = props[1] if props is not None and props[0] == 200 and isinstance(props[1], dict) else {}
        ctx, lanes = _n_ctx(answer), _lanes(answer.get("total_slots"))
        try:
            port = int(root.rsplit(":", 1)[1])
        except (IndexError, ValueError):
            port = None
        for m in models:
            view["servers"].append({"name": m["id"], "model": m.get("root") or m["id"], "cards": None, "port": port,
                                    "ctx": ctx, "state": "ready" if health is not None and health[0] == 200
                                    else "unknown", "pid": None, "restarts": None, "lanes": lanes})
    view["main"] = machx.choose_main(models, view["default"], machx.main_role())
    return view


def describe(view: dict) -> list[str]:
    """One line for the endpoint and one per server, for a terminal."""
    if not view["reachable"]:
        return [f"engine at {view['endpoint']}: not answering" + (f" -- {view['notes'][0]}" if view["notes"] else "")]
    head = ("supervised layout" if view["supervised"] else "one engine") + f" at {view['endpoint']}"
    if view["status"]:
        head += f" · {view['status']}"
    if view["default"]:
        head += f" · default {view['default']}"
    lines = [head]
    for s in view["servers"]:
        cards = ("card " + ",".join(str(c) for c in s["cards"])) if s["cards"] else "cards unreported"
        parts = [s["name"], cards, f"port {s['port']}" if s["port"] is not None else "port unreported",
                 f"ctx {s['ctx']}" if s["ctx"] is not None else "ctx unreported", s["state"]]
        if s["model"] and s["model"] != s["name"]:
            parts.insert(1, s["model"])
        if (s.get("lanes") or 1) > 1:
            parts.append(f"{s['lanes']} lanes")
        if s["name"] == view["main"]:
            parts.append("main")
        lines.append(" · ".join(parts))
    return lines
