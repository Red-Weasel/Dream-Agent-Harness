"""Sleepwalk connectors (DREAM-160): one Python file each, exporting

    CONNECTOR = {"id", "label", "kinds": ["context" and/or "notify"], "fields": [{"name", "label", "secret"?, "default"?}]}
    fetch(cfg) -> str                 (context: read-only text put before the instructions)
    send(cfg, title, body) -> None    (notify: to the owner's own configured address or chat only)

Built-ins live beside this file. An enabled plugin's `sleepwalk/*.py` is imported only from the exact source the
owner reviewed and trusted (extensions.approved_module_source), like a plugin tool."""
from __future__ import annotations

import asyncio
import importlib
import logging
import re
import threading
from pathlib import Path
from types import ModuleType

from .. import credentials, store

KINDS = {"context", "notify"}
CONTEXT_CAP = 8000                            # characters of one connector's context in a run's prompt
_QUIET = threading.local()
logging.getLogger("httpx").addFilter(lambda record: not getattr(_QUIET, "on", False))
_ID = re.compile(r"^[a-z][a-z0-9_]{0,31}\Z")


def _valid(module) -> bool:
    c = getattr(module, "CONNECTOR", None)
    try:
        return (bool(_ID.match(c["id"])) and isinstance(c["label"], str) and bool(c["kinds"])
                and set(c["kinds"]) <= KINDS and all(_ID.match(f["name"]) for f in c["fields"])
                and all(callable(getattr(module, "fetch" if k == "context" else "send", None)) for k in c["kinds"]))
    except (TypeError, KeyError):
        return False


def discover(warnings: list[str] | None = None) -> dict[str, ModuleType]:
    from ... import extensions, plugins
    warnings = warnings if warnings is not None else []
    modules = [importlib.import_module(f"{__name__}.{p.stem}") for p in sorted(Path(__file__).parent.glob("[!_]*.py"))]
    if not plugins.loaded():
        plugins.load()
    for plugin in (p for p in plugins.loaded() if p.enabled):
        for path in sorted((plugin.path / "sleepwalk").glob("[!_]*.py")):
            try:
                source, _ = extensions.approved_module_source(path, plugin.name)
                module = ModuleType(f"dream.plugins.{plugin.name}.sleepwalk.{path.stem}")
                module.__file__ = str(path)
                exec(compile(source, str(path), "exec"), module.__dict__)
                modules.append(module)
            except (Exception, SystemExit) as exc:
                warnings.append(f"plugin {plugin.name}: connector {path.name} was not loaded: {exc}")
    found: dict[str, ModuleType] = {}
    for module in modules:
        if not _valid(module) or module.CONNECTOR["id"] in found:
            warnings.append(f"{getattr(module, '__file__', module.__name__)}: not a usable connector (or a duplicate id)")
            continue
        found[module.CONNECTOR["id"]] = module
    return found


def config(module: ModuleType) -> dict:
    c, saved = module.CONNECTOR, store.connector_settings().get(module.CONNECTOR["id"], {})
    return {f["name"]: (credentials.get(c["id"], f["name"]) if f.get("secret") else saved.get(f["name"]))
            or f.get("default") for f in c["fields"]}


def describe(found: dict[str, ModuleType]) -> list[dict]:
    rows = []
    for cid, module in found.items():
        values, saved = config(module), store.connector_settings().get(cid, {})
        fields = [{"name": f["name"], "label": f.get("label", f["name"]), "secret": bool(f.get("secret")),
                   **({"set": credentials.where(cid, f["name"])} if f.get("secret") else
                      {"value": saved.get(f["name"], ""), "default": f.get("default", "")})}
                  for f in module.CONNECTOR["fields"]]
        rows.append({"id": cid, "label": module.CONNECTOR["label"], "kinds": module.CONNECTOR["kinds"],
                     "ready": all(values.values()), "fields": fields})
    return rows


def save(found: dict[str, ModuleType], cid: str, values: dict) -> None:
    module = found.get(cid)
    if module is None or not isinstance(values, dict):
        raise KeyError(cid)
    plain = {}
    for f in module.CONNECTOR["fields"]:
        value = values.get(f["name"])
        if not isinstance(value, str):
            continue                      # untouched
        value = value.strip()             # a pasted trailing newline
        if len(value) > 2000 or not value.isprintable() or value and not re.fullmatch(f.get("pattern", ".*"), value):
            raise ValueError(f"{f.get('label', f['name'])}: not a valid value")
        if f.get("secret"):
            if value:                     # an empty secret box keeps what is stored
                credentials.put(cid, f["name"], value)
        else:
            plain[f["name"]] = value
    store.save_connector_settings(cid, plain)


async def _call(module: ModuleType, name: str, *args):
    """Run a connector call off the event loop; its error text never carries a secret (a token in a URL, say)."""
    cfg = config(module)
    if not all(cfg.values()):
        raise RuntimeError("not set up; open Connectors in the editor")
    def quiet():                          # httpx logs request URLs, which can hold a secret
        _QUIET.on = True
        try:
            return getattr(module, name)(cfg, *args)
        finally:
            _QUIET.on = False
    try:
        return await asyncio.wait_for(asyncio.to_thread(quiet), timeout=60)
    except Exception as exc:
        text = f"{type(exc).__name__}: {exc}"
        for f in module.CONNECTOR["fields"]:
            if f.get("secret") and cfg.get(f["name"]):
                text = text.replace(cfg[f["name"]], "[secret]")
        raise RuntimeError(text[:300]) from None


async def gather(found: dict[str, ModuleType], ids: list[str]) -> tuple[str, list[str]]:
    """The read-only context for a run, and what could not be read."""
    parts, errors = [], []
    for cid in ids:
        module = found.get(cid)
        try:
            if module is None or "context" not in module.CONNECTOR["kinds"]:
                raise LookupError("not an installed context connector")
            text = (await _call(module, "fetch")).strip()
            text = text if len(text) <= CONTEXT_CAP else text[:CONTEXT_CAP] + "\n[... cut: the rest was not included]"
            parts.append(f"## {module.CONNECTOR['label']} (read-only)\n{text}")
        except Exception as exc:
            errors.append(f"{cid}: {exc}")
    return "\n\n".join(parts), errors


async def notify(found: dict[str, ModuleType], channels: list[str], title: str, body: str,
                 allowed=None) -> tuple[list[str], list[str]]:
    """Send to each channel; `allowed` (an async check, e.g. the owner is still logged in) is asked before each one."""
    sent, errors = ["app"], []
    for cid in (c for c in channels if c != "app"):
        module = found.get(cid)
        if allowed is not None and not await allowed():
            errors.append(f"{cid}: not sent: you logged out")
            continue
        try:
            if module is None or "notify" not in module.CONNECTOR["kinds"]:
                raise LookupError("not an installed notification connector")
            await _call(module, "send", title, body)
            sent.append(cid)
        except Exception as exc:
            errors.append(f"{cid}: {exc}")
    return sent, errors
