"""Dream-owned extension settings, catalog, and runtime availability gates.

Discovery is read-only. No migration rewrites an installed package. Missing
settings preserve the existing defaults; corrupt settings disable extensions
and are never silently replaced. Observations are explicit counters, not an
inference that an extension has never been used elsewhere.
"""
from __future__ import annotations

import copy
import hashlib
import io
import json
import os
import stat
import tempfile
import threading
import tokenize
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

from . import config

KINDS = frozenset({"skill", "plugin", "mcp", "hook", "tool"})
OBSERVATIONS = frozenset({"opened", "file_read", "started", "attempted", "completed", "failed", "cancelled", "blocked"})
MAX_SETTINGS_BYTES = 2_000_000
MAX_MODULE_BYTES = 262_144
_LOCK = threading.RLock()
_RUNTIME: dict[str, dict[str, Any]] = {}
_USAGE_WARNINGS: list[str] = []


class SettingsError(ValueError):
    """The settings must be repaired explicitly, without discarding their data."""


def settings_path() -> Path:
    return Path(os.environ.get("DREAM_EXTENSION_SETTINGS", str(config.DATA_DIR / "config" / "extensions.json"))).expanduser()


def extension_id(kind: str, name: str) -> str:
    if kind not in KINDS or not isinstance(name, str) or not name.strip():
        raise ValueError("extension id needs a supported kind and a nonempty name")
    name = name.strip().casefold()
    if len(name) > 240 or any(ord(c) < 32 for c in name):
        raise ValueError("invalid extension name")
    return kind + ":" + name


def _id(value: str) -> str:
    if not isinstance(value, str) or ":" not in value:
        raise ValueError("use a qualified id such as skill:verifying or plugin:example")
    return extension_id(*value.split(":", 1))


def _empty() -> dict[str, Any]:
    return {"version": 1, "overrides": {}, "usage": {}, "hooks": {}, "trusted_hooks": {}, "trusted_modules": {}}


def _unique_object(pairs):
    obj = {}
    for key, value in pairs:
        if key in obj:
            raise ValueError(f"duplicate JSON key: {key}")
        obj[key] = value
    return obj


def _nonfinite(value):
    raise ValueError(f"non-finite JSON value: {value}")


def _read() -> dict[str, Any]:
    path = settings_path()
    try:
        with path.open("rb") as f:
            raw = f.read(MAX_SETTINGS_BYTES + 1)
    except FileNotFoundError:
        return _empty()
    except OSError as e:
        raise SettingsError(f"extension settings unreadable: {path} ({type(e).__name__})") from e
    try:
        if len(raw) > MAX_SETTINGS_BYTES:
            raise ValueError("file exceeds size limit")
        data = json.loads(raw, object_pairs_hook=_unique_object, parse_constant=_nonfinite)
        if not isinstance(data, dict) or type(data.get("version")) is not int or data["version"] != 1:
            raise ValueError("expected version 1 object")
        for key in ("overrides", "usage", "hooks", "trusted_hooks", "trusted_modules"):
            if not isinstance(data.get(key, {}), dict):
                raise ValueError(f"{key} must be an object")
            data.setdefault(key, {})
        for key, enabled in data["overrides"].items():
            if _id(key) != key or type(enabled) is not bool:
                raise ValueError("overrides require canonical extension ids and boolean values")
        for key, row in data["usage"].items():
            if _id(key) != key or not isinstance(row, dict) or not isinstance(row.get("counts"), dict):
                raise ValueError("invalid usage entry")
            if any(k not in OBSERVATIONS or type(v) is not int or v < 0 for k, v in row["counts"].items()):
                raise ValueError("invalid observation counter")
            for stamp in ("first_observed_at", "last_observed_at"):
                if not isinstance(row.get(stamp), str):
                    raise ValueError("usage timestamps must be strings")
        for key, fingerprint in data["trusted_hooks"].items():
            if _id(key) != key or not key.startswith("hook:") or not isinstance(fingerprint, str) or len(fingerprint) != 64:
                raise ValueError("invalid trusted hook fingerprint")
        for key, row in data["trusted_modules"].items():
            if (not _module_id(key) or not isinstance(row, dict)
                    or not isinstance(row.get("path"), str) or not Path(row["path"]).is_absolute()
                    or not _sha256(row.get("sha256")) or not isinstance(row.get("approved_at"), str)):
                raise ValueError("invalid trusted module snapshot")
        if any(not isinstance(k, str) or extension_id("hook", k) != "hook:" + k or not isinstance(v, dict) for k, v in data["hooks"].items()):
            raise ValueError("invalid hook registration")
        return data
    except (ValueError, TypeError, UnicodeError) as e:
        raise SettingsError(f"extension settings invalid: {path}: {e}; repair this file explicitly") from e


def settings() -> dict[str, Any]:
    """Read the current settings; raises SettingsError on malformed input."""
    with _LOCK:
        return _read()


@contextmanager
def _transaction():
    """Cross-thread/process read-modify-write. Replace atomically; mode 0600."""
    import fcntl

    path = settings_path()
    with _LOCK:
        path.parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(str(path) + ".lock", os.O_CREAT | os.O_RDWR, 0o600)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX)
            data = _read()
            yield data
            raw = (json.dumps(data, ensure_ascii=False, indent=2, sort_keys=True, allow_nan=False) + "\n").encode()
            if len(raw) > MAX_SETTINGS_BYTES:
                raise SettingsError("extension settings exceed size limit; no changes saved")
            temp_fd, temp_name = tempfile.mkstemp(prefix=".extensions-", dir=path.parent)
            try:
                with os.fdopen(temp_fd, "wb") as out:
                    out.write(raw)
                    out.flush()
                    os.fsync(out.fileno())
                os.replace(temp_name, path)
                directory = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
                try:
                    os.fsync(directory)
                finally:
                    os.close(directory)
            finally:
                try:
                    os.unlink(temp_name)
                except FileNotFoundError:
                    pass
        finally:
            os.close(fd)


def is_enabled(identifier: str, default: bool = True, *, parent: str | None = None) -> bool:
    """Cheap runtime check, including after discovery; corrupt settings deny."""
    identifier = _id(identifier)
    # MCP clients may only know the namespaced tool name. The pre-start config
    # filter recorded its owner, so disabling a plugin still gates that tool.
    runtime = _RUNTIME.get(identifier, {})
    if parent is None and runtime.get("settings_path") == str(settings_path()):
        parent = runtime.get("parent")
    try:
        data = settings()
    except SettingsError:
        return False
    enabled = data["overrides"].get(identifier, False if identifier.startswith("hook:") else default)
    if not enabled:
        return False
    if parent and not is_enabled(parent, _parent_default(parent)):
        return False
    if identifier.startswith("hook:"):
        from .hooks import fingerprint, validate_spec

        try:
            spec = validate_spec(data["hooks"][identifier.split(":", 1)[1]])
            return data["trusted_hooks"].get(identifier) == fingerprint(spec)
        except (KeyError, ValueError, OSError):
            return False
    return True


def _parent_default(identifier: str) -> bool:
    if identifier.startswith("plugin:"):
        from . import plugins

        for p in plugins.loaded():
            if identifier == extension_id("plugin", p.name):
                return p.default_enabled
    return True


def set_enabled(identifier: str, enabled: bool, *, trusted: bool = False) -> dict[str, Any]:
    """Persist an explicit override. Hook enable requires trust of its spec.

    This is a user-control API, not a model tool or a permission grant. A caller
    enabling executable hooks must show the command and obtain user consent.
    """
    identifier = _id(identifier)
    if type(enabled) is not bool or type(trusted) is not bool:
        raise ValueError("enabled and trusted must be booleans")
    with _transaction() as data:
        if identifier.startswith("hook:") and enabled:
            from .hooks import fingerprint, validate_spec

            raw = data["hooks"].get(identifier.split(":", 1)[1])
            spec = validate_spec(raw)
            digest = fingerprint(spec)
            if trusted:
                data["trusted_hooks"][identifier] = digest
            elif data["trusted_hooks"].get(identifier) != digest:
                raise ValueError("hook must be explicitly trusted before enabling (trusted=True)")
        data["overrides"][identifier] = enabled
    result = {"id": identifier, "override": enabled, "enabled": is_enabled(identifier)}
    if _module_id(identifier):
        result["configured_enabled"] = result["enabled"]
        try:
            path, owner = _configured_module(identifier)
            result["enabled"] = result["enabled"] and module_status(path, owner)["trust_state"] == "trusted"
        except (OSError, ValueError):
            result["enabled"] = False
    return result


def clear_override(identifier: str) -> None:
    """Return to package defaults. Hooks default off even after prior trust."""
    with _transaction() as data:
        data["overrides"].pop(_id(identifier), None)


def record_usage(identifier: str, observation: str = "completed") -> bool:
    """Record a witnessed runtime event. Failure is reported, never fabricated."""
    identifier = _id(identifier)
    if observation not in OBSERVATIONS:
        raise ValueError("unknown usage observation")
    now = datetime.now(timezone.utc).isoformat()
    try:
        with _transaction() as data:
            row = data["usage"].setdefault(identifier, {"counts": {}, "first_observed_at": now, "last_observed_at": now})
            row["counts"][observation] = row["counts"].get(observation, 0) + 1
            row["last_observed_at"] = now
        return True
    except (SettingsError, OSError) as e:
        warning = f"Usage could not be recorded ({type(e).__name__}); observed counters may be incomplete"
        if warning not in _USAGE_WARNINGS:
            _USAGE_WARNINGS.append(warning)
        return False


def usage(identifier: str) -> dict[str, Any]:
    try:
        row = settings()["usage"].get(_id(identifier))
    except SettingsError:
        return {"state": "unavailable", "counts": {}}
    return {"state": "observed", **copy.deepcopy(row)} if row else {"state": "not_observed", "counts": {}}


def register_runtime(identifier: str, **metadata: Any) -> None:
    """Register provenance for tools already loaded by their authorized loader."""
    _RUNTIME[_id(identifier)] = {"settings_path": str(settings_path()), **metadata}


def tool_module_id(path: Path, plugin: str | None = None) -> str:
    return extension_id("tool", f"plugin/{plugin}/{path.stem}" if plugin else f"custom/{path.stem}")


def _module_id(identifier: str) -> bool:
    return (isinstance(identifier, str) and identifier == _id(identifier)
            and (identifier.startswith("tool:custom/") or identifier.startswith("tool:plugin/")))


def _sha256(value: Any) -> bool:
    return isinstance(value, str) and len(value) == 64 and all(c in "0123456789abcdef" for c in value)


def _module_source(path: Path, plugin: str | None = None) -> tuple[bytes, dict]:
    """Read a bounded primary source file once; never execute a loader/pyc."""
    path = Path(path)
    if path.suffix != ".py" or path.name.startswith("_"):
        raise SettingsError("Review a configured Python tool module")
    # Canonical parent identity revokes trust if a configured root is retargeted.
    canonical = path.parent.resolve(strict=True) / path.name
    fd = os.open(canonical, os.O_RDONLY | os.O_NONBLOCK | os.O_NOFOLLOW)
    with os.fdopen(fd, "rb") as source:
        info = os.fstat(source.fileno())
        if not stat.S_ISREG(info.st_mode) or info.st_size > MAX_MODULE_BYTES:
            raise SettingsError(f"Python tool source must be a regular file up to {MAX_MODULE_BYTES} bytes")
        raw = source.read(MAX_MODULE_BYTES + 1)
    if len(raw) > MAX_MODULE_BYTES:
        raise SettingsError(f"Python tool source exceeds {MAX_MODULE_BYTES} bytes")
    return raw, {"id": tool_module_id(path, plugin), "path": str(canonical),
                 "sha256": hashlib.sha256(raw).hexdigest(), "size": len(raw)}


def _configured_module(identifier: str) -> tuple[Path, str | None]:
    from . import plugins

    identifier = _id(identifier)
    if not _module_id(identifier):
        raise SettingsError("Use a module id: tool:custom/name or tool:plugin/owner/name")
    # Disabled modules/plugins can be reviewed without enabling or importing them.
    if not plugins.loaded():
        plugins.load()
    roots = [(config.CUSTOM_TOOLS_DIR, None)] + [(p.tool_dir, p.name) for p in plugins.loaded() if p.tool_dir]
    matches = [(path, owner) for root, owner in roots for path in root.glob("*.py")
               if not path.name.startswith("_") and tool_module_id(path, owner) == identifier]
    if len(matches) != 1:
        raise SettingsError(f"{identifier}: expected one installed module, found {len(matches)}")
    return matches[0]


def review_module(identifier: str) -> dict[str, Any]:
    """Human control: return exact source and SHA-256, without importing code."""
    path, owner = _configured_module(identifier)
    raw, snapshot = _module_source(path, owner)
    try:
        encoding, _ = tokenize.detect_encoding(io.BytesIO(raw).readline)
        source = raw.decode(encoding)
    except (SyntaxError, UnicodeError, LookupError) as exc:
        raise SettingsError(f"{identifier}: cannot decode source for review: {exc}") from exc
    return {**snapshot, "source": source}


def trust_module(identifier: str, expected_sha256: str) -> dict[str, Any]:
    """Approve a reviewed primary source snapshot, without changing enablement.

    This user-control API is deliberately absent from model tool schemas. It is
    not isolation: approved Python imports and dependencies remain trusted host
    code. No timestamps/usage/install-presence heuristics create legacy trust.
    """
    if not _sha256(expected_sha256):
        raise SettingsError("Provide the SHA-256 from the source you reviewed")
    path, owner = _configured_module(identifier)
    with _transaction() as data:
        _, snapshot = _module_source(path, owner)
        if snapshot["sha256"] != expected_sha256:
            raise SettingsError("Module changed since review; inspect the new source before trusting it")
        data["trusted_modules"][snapshot["id"]] = {
            "path": snapshot["path"], "sha256": snapshot["sha256"],
            "approved_at": datetime.now(timezone.utc).isoformat(),
        }
    return {**snapshot, "trusted": True}


def _snapshot_trusted(snapshot: dict, data: dict) -> bool:
    trusted = data["trusted_modules"].get(snapshot["id"], {})
    return trusted.get("path") == snapshot["path"] and trusted.get("sha256") == snapshot["sha256"]


def module_status(path: Path, plugin: str | None = None) -> dict[str, Any]:
    """Catalog metadata. Enabled configuration and import trust are separate."""
    try:
        _, snapshot = _module_source(path, plugin)
        data = settings()
        trusted = _snapshot_trusted(snapshot, data)
        return {"trust_state": "trusted" if trusted else "changed" if snapshot["id"] in data["trusted_modules"] else "required",
                "sha256": snapshot["sha256"], "source_path": snapshot["path"]}
    except (OSError, ValueError) as exc:
        return {"trust_state": "unavailable", "trust_error": str(exc)}


def approved_module_source(path: Path, plugin: str | None = None) -> tuple[bytes, dict]:
    """Registry gate: return the exact approved bytes or refuse before import."""
    raw, snapshot = _module_source(path, plugin)
    parent = extension_id("plugin", plugin) if plugin else None
    if not is_enabled(snapshot["id"], parent=parent) or not _snapshot_trusted(snapshot, settings()):
        raise SettingsError(f"{snapshot['id']}: Python import blocked; review and explicitly trust the current source SHA-256")
    return raw, snapshot


def tool_enabled(tool: Any) -> bool:
    identifier = extension_id("tool", tool.name)
    if not is_enabled(identifier):
        return False
    owner = getattr(tool, "_dream_extension_id", None)
    parent = getattr(tool, "_dream_plugin_id", None)
    if owner and not is_enabled(owner, parent=parent):
        return False
    if parent and not is_enabled(parent, _parent_default(parent)):
        return False
    if owner and _module_id(owner):
        # Cached handlers from a previously approved version do not inherit a
        # later version's approval. A new build must load that exact snapshot.
        source = getattr(tool, "_dream_source_path", None)
        digest = getattr(tool, "_dream_module_sha256", None)
        if not source or not digest:
            return False
        plugin = parent.split(":", 1)[1] if parent else None
        current = module_status(Path(source), plugin)
        if current["trust_state"] != "trusted" or current.get("sha256") != digest:
            return False
    # External MCP tools use <server>__<tool>; no such names are accepted for
    # custom tools. Also support tools explicitly marked by the MCP client.
    server = getattr(tool, "_dream_mcp_server", None)
    if server is None and "__" in tool.name:
        server = tool.name.split("__", 1)[0]
    return not server or is_enabled(extension_id("mcp", server), parent=parent)


def filter_tools(tools: Iterable[Any]) -> list[Any]:
    """Call again before every provider request to remove freshly disabled tools."""
    return [tool for tool in tools if tool_enabled(tool)]


def guard_tool(tool: Any) -> Any:
    """A fresh SDK tool whose handler rechecks toggles, even if cached by a backend.

    Does not run hooks or make policy decisions. The engine still owns both.
    """
    import asyncio
    from functools import wraps
    from claude_agent_sdk import SdkMcpTool

    if getattr(tool, "_dream_extension_guard", False):
        return tool
    identifier = extension_id("tool", tool.name)
    owner = getattr(tool, "_dream_extension_id", None)
    parent = getattr(tool, "_dream_plugin_id", None)
    server = getattr(tool, "_dream_mcp_server", None)
    if not server and "__" in tool.name:
        server = tool.name.split("__", 1)[0]
    if server and not parent:
        provenance = _RUNTIME.get(extension_id("mcp", server), {})
        if provenance.get("settings_path") == str(settings_path()):
            parent = provenance.get("parent")
            if parent:
                tool._dream_plugin_id = parent
    observed = list(dict.fromkeys(x for x in (identifier, owner, parent, extension_id("mcp", server) if server else None) if x))
    register_runtime(identifier, name=tool.name, kind="tool", parent=parent,
                     source=getattr(tool, "_dream_source_path", "dream-runtime"), owner=owner,
                     provenance="mcp-tool" if server else "plugin-tool" if parent else "dream-tool")

    @wraps(tool.handler)
    async def handler(args):
        if not tool_enabled(tool):
            return {"content": [{"type": "text", "text": f"{tool.name}: extension disabled or executable source trust changed in Dream settings"}], "is_error": True}
        for item in observed:
            record_usage(item, "attempted")
        try:
            result = await tool.handler(args)
        except asyncio.CancelledError:
            for item in observed:
                record_usage(item, "cancelled")
            raise
        except Exception:
            for item in observed:
                record_usage(item, "failed")
            raise
        for item in observed:
            record_usage(item, "failed" if isinstance(result, dict) and (result.get("is_error") or result.get("isError")) else "completed")
        return result

    wrapped = SdkMcpTool(name=tool.name, description=tool.description, input_schema=tool.input_schema,
                         handler=handler, annotations=getattr(tool, "annotations", None))
    for attr in ("_dream_extension_id", "_dream_plugin_id", "_dream_mcp_server", "_dream_source_path", "_dream_module_sha256"):
        if hasattr(tool, attr):
            setattr(wrapped, attr, getattr(tool, attr))
    wrapped._dream_extension_guard = True
    return wrapped


def filter_mcp_configs(configs: Iterable[dict[str, Any]], *, plugin: str | None = None) -> list[dict[str, Any]]:
    """Call BEFORE McpClients.start; settings never start a disabled server.

    Keep provenance keys when merging: _dream_plugin names the owning plugin.
    Command, args and environment are never copied into catalog/status output.
    """
    out = []
    for cfg in configs:
        parent_name = plugin or cfg.get("_dream_plugin")
        parent = extension_id("plugin", parent_name) if parent_name else None
        identifier = extension_id("mcp", cfg["name"])
        register_runtime(identifier, name=cfg["name"], kind="mcp", parent=parent,
                         source=cfg.get("_dream_source", str(config.MCP_CONFIG_PATH)))
        if is_enabled(identifier, parent=parent):
            out.append(dict(cfg))
    return out


def _row(identifier: str, name: str, *, default: bool = True, parent: str | None = None, **metadata: Any) -> dict[str, Any]:
    identifier = _id(identifier)
    return {"id": identifier, "kind": identifier.split(":", 1)[0], "name": name,
            "enabled": is_enabled(identifier, default, parent=parent), "default_enabled": default,
            "parent": parent, "usage": usage(identifier), **metadata}


def catalog(*, refresh: bool = False) -> dict[str, Any]:
    """JSON-ready catalog for CLI/GUI. Reads metadata; never imports tool code."""
    from . import plugins
    from .tools import installed_skill_tools
    from .mcp_client import load_config

    rows: dict[str, dict[str, Any]] = {}
    warnings = []
    if refresh or not plugins.loaded():
        plugins.load()
    for p in plugins.loaded():
        identifier = extension_id("plugin", p.name)
        rows[identifier] = _row(identifier, p.name, default=p.default_enabled,
                                source=str(p.path), description=p.description, version=p.version,
                                capabilities=p.capabilities, provenance="dream-plugin")
    # Inventory includes disabled plugin roots, while the model-facing index
    # and open/file paths always apply the effective enable gates.
    for s in installed_skill_tools.inventory(refresh=refresh):
        identifier = extension_id("skill", s.name)
        rows[identifier] = _row(identifier, s.name, parent=s.parent_extension,
                                source=s.source, path=str(s.root), description=s.description,
                                capabilities=list(s.capabilities), portable=s.portable,
                                provenance=s.provenance, version=s.version)
    warnings.extend(plugins.warnings())
    warnings.extend(installed_skill_tools.warnings())
    roots = [(config.CUSTOM_TOOLS_DIR, None)] + [(p.tool_dir, p.name) for p in plugins.loaded() if p.tool_dir]
    for root, parent_name in roots:
        for path in sorted(root.glob("*.py")):
            if path.name.startswith("_"):
                continue
            identifier = tool_module_id(path, parent_name)
            parent = extension_id("plugin", parent_name) if parent_name else None
            trust = module_status(path, parent_name)
            row = _row(identifier, path.stem, parent=parent, source=str(path), provenance="python-tool-module", **trust)
            row["configured_enabled"] = row["enabled"]
            row["enabled"] = row["enabled"] and trust["trust_state"] == "trusted"
            rows[identifier] = row
    root_mcp, w = load_config(config.MCP_CONFIG_PATH)
    warnings.extend(w)
    mcp_rows = [(s, None, str(config.MCP_CONFIG_PATH)) for s in root_mcp]
    for p in plugins.loaded():
        configs, w = load_config(p.path / "mcp.json")
        warnings.extend(w)
        mcp_rows.extend((s, p.name, str(p.path / "mcp.json")) for s in configs)
    for spec, parent_name, source in mcp_rows:
        identifier = extension_id("mcp", spec["name"])
        if identifier in rows:
            warnings.append(f"{identifier}: duplicate configuration from {source}; first source wins")
            continue
        parent = extension_id("plugin", parent_name) if parent_name else None
        rows[identifier] = _row(identifier, spec["name"], parent=parent, source=source, provenance="mcp-config")
    try:
        data = settings()
        from .hooks import validate_spec

        for name, raw in data["hooks"].items():
            identifier = extension_id("hook", name)
            try:
                spec = validate_spec(raw)
                rows[identifier] = _row(identifier, name, default=False, source=str(settings_path()),
                                        provenance="explicit-registration", events=spec["events"], mode=spec["mode"],
                                        command=spec["command"], cwd=spec["cwd"], timeout_s=spec["timeout_s"])
            except ValueError as e:
                warnings.append(f"{identifier}: {e}")
        for identifier, enabled in data["overrides"].items():
            if identifier not in rows and identifier not in _RUNTIME:
                rows[identifier] = _row(identifier, identifier.split(":", 1)[1], installed=False,
                                        provenance="saved-override", source=str(settings_path()))
            if identifier in rows:
                rows[identifier]["override"] = enabled
    except SettingsError as e:
        warnings.append(str(e))
    for identifier, info in _RUNTIME.items():
        if identifier in rows or info.get("settings_path") != str(settings_path()):
            continue
        meta = {k: v for k, v in info.items() if k not in {"name", "kind", "settings_path", "parent"}}
        rows[identifier] = _row(identifier, info.get("name", identifier.split(":", 1)[1]), parent=info.get("parent"), **meta)
    for row in rows.values():
        owner = rows.get(row.get("owner"))
        if owner and not owner["enabled"]:
            row["configured_enabled"] = row["enabled"]
            row["enabled"] = False
            row["blocked_by"] = owner["id"]
    counts = {kind: {"total": 0, "enabled": 0, "disabled": 0} for kind in sorted(KINDS)}
    for row in rows.values():
        tally = counts[row["kind"]]
        tally["total"] += 1
        tally["enabled" if row["enabled"] else "disabled"] += 1
    return {"extensions": sorted(rows.values(), key=lambda r: r["id"]), "counts": counts,
            "warnings": list(dict.fromkeys(warnings + _USAGE_WARNINGS)), "settings_path": str(settings_path()),
            "usage_note": "Counters cover observed Dream runtime events only; absent history means not observed."}


def status(*, refresh: bool = False) -> dict[str, Any]:
    return catalog(refresh=refresh)
