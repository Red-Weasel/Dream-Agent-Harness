"""Explicit local HTTP fixture evaluation, isolated from operator configuration.

Uses the existing compatible backend and checked phase runner. Never starts a
model, Engine, native CLI, plugin catalog, or external provider. Live use is opt-in;
synthetic transport tests are not model-quality or endurance qualification.
"""
from __future__ import annotations

import asyncio
from dataclasses import asdict, dataclass
import hashlib
import ipaddress
import json
import os
from pathlib import Path
import subprocess
import sys
import time
from urllib.parse import urlsplit

_SOURCE_FILES = ("dream/local_harness_eval.py", "dream/local_eval_catalog.py", "dream/local_eval_skills.py", "dream/harness_eval.py",
                 "dream/eval_fixtures/harness_cases.json", "dream/core/backends/openai_compat.py",
                 "dream/core/phase_runner.py", "dream/core/loop.py", "dream/core/evaluator.py",
                 "dream/core/task_checkpoints.py", "dream/core/run_state.py", "dream/tools/native.py",
                 "dream/tools/registry.py", "dream/tools/demonstration_tools.py", "dream/tools/capability_tools.py")


def _packaged_root():
    source_root = Path(__file__).resolve().parents[1]
    checkout = source_root / "skills"
    return checkout if checkout.exists() else Path(__file__).parent / "resources" / "skills"


def _source_hashes(*, include_skills=False):
    source_root = Path(__file__).resolve().parents[1]
    hashes = {name: hashlib.sha256((source_root / name).read_bytes()).hexdigest() for name in _SOURCE_FILES}
    if include_skills:
        from .local_eval_skills import load_packaged_skills
        root = _packaged_root()
        prefix = root.relative_to(source_root).as_posix()
        hashes.update({prefix + "/" + row.source: row.sha256 for row in load_packaged_skills(root)})
    return hashes


def _observe_sse(row: dict, line: bytes) -> None:
    """Observe before yielding: the backend can close at DONE without EOF."""
    if not line.startswith(b"data:"):
        return
    value = line[5:].strip()
    if value == b"[DONE]":
        row["stream_complete"] = True
        return
    try:
        data = json.loads(value)
    except ValueError:
        return
    if not isinstance(data, dict):
        return
    usage = data.get("usage")
    if isinstance(usage, dict):
        kept = {}
        for key in ("prompt_tokens", "completion_tokens", "total_tokens", "input_tokens", "output_tokens", "cached_tokens"):
            number = usage.get(key)
            if type(number) is int and 0 <= number <= 2**53:
                kept[key] = number
        details = usage.get("prompt_tokens_details")
        if isinstance(details, dict) and type(details.get("cached_tokens")) is int and 0 <= details["cached_tokens"] <= 2**53:
            kept["prompt_tokens_details"] = {"cached_tokens": details["cached_tokens"]}
        row["usage"] = kept or None
    if isinstance(data.get("model"), str) and len(data["model"]) <= 256:
        if row.get("reported_model") is None:
            row["reported_model"] = data["model"]
        elif row["reported_model"] != data["model"]:
            row["reported_model_conflict"] = True


@dataclass(frozen=True)
class LocalEvalConfig:
    endpoint: str
    model: str
    discovery_mode: str = "default"
    max_requests: int = 24
    max_wall_seconds: int = 300
    catalog_mode: str = "fixture"

    def __post_init__(self):
        if not isinstance(self.endpoint, str):
            raise ValueError("An explicit numeric-loopback HTTP endpoint is required")
        try:
            url = urlsplit(self.endpoint)
            address = ipaddress.ip_address(url.hostname or "")
            port = url.port
        except ValueError as exc:
            raise ValueError("An explicit numeric-loopback HTTP endpoint is required") from exc
        if (url.scheme != "http" or not address.is_loopback or port is None or port == 0
                or url.username is not None or url.password is not None
                or url.query or url.fragment or url.path not in ("/v1", "/v1/")
                or any(c.isspace() for c in self.endpoint)):
            raise ValueError("Use http://<numeric-loopback>:<port>/v1 without credentials or query")
        if (not isinstance(self.model, str) or not self.model.strip() or len(self.model) > 256
                or any(ord(c) < 32 for c in self.model)):
            raise ValueError("A bounded explicit model name is required")
        if self.discovery_mode not in ("default", "descriptions"):
            raise ValueError("discovery_mode must be default or descriptions")
        if self.catalog_mode not in ("fixture", "builtin_tools", "builtin_tools_skills"):
            raise ValueError("catalog_mode must be fixture, builtin_tools or builtin_tools_skills")
        for name, low, high in (("max_requests", 1, 64), ("max_wall_seconds", 1, 600)):
            value = getattr(self, name)
            if type(value) is not int or not low <= value <= high:
                raise ValueError("Invalid local fixture budget: " + name)


def child_environment(root: Path, config: LocalEvalConfig) -> dict[str, str]:
    """An allowlist, never the operator's environment with a few keys removed."""
    root = root.resolve()
    env = {
        "PATH": "/usr/bin:/bin", "LANG": "C.UTF-8", "PYTHONNOUSERSITE": "1",
        "PYTHONPATH": str(Path(__file__).resolve().parents[1]),
        "HOME": str(root / "home"), "DREAM_ROOT": str(root),
        "XDG_CONFIG_HOME": str(root / "config"), "XDG_CACHE_HOME": str(root / "cache"),
        "XDG_DATA_HOME": str(root / "data-home"), "XDG_RUNTIME_DIR": str(root / "runtime"),
        "DREAM_SKILL_DIRS": "", "DREAM_SEMANTIC_MEMORY": "0", "DREAM_RERANK": "0",
        "DREAM_CONSOLIDATE": "0", "DREAM_CONTEXT_COUNTER": "off",
        "DREAM_DISCOVERY_MODE": config.discovery_mode, "DREAM_MAX_TOKENS": "2048",
        "DREAM_LLM_READ_TIMEOUT_S": str(config.max_wall_seconds),
        "CUDA_VISIBLE_DEVICES": "", "HIP_VISIBLE_DEVICES": "", "ROCR_VISIBLE_DEVICES": "",
    }
    # Operational endpoint coordination is shared intentionally. Do not copy
    # pytest's flags or create a private lease namespace that hides live clients.
    if not (os.environ.get("PYTEST_CURRENT_TEST") or os.environ.get("PYTEST_VERSION")):
        lease = os.environ.get("DREAM_INFERENCE_LEASE_DIR")
        if lease:
            if not Path(lease).is_absolute():
                raise ValueError("Shared inference lease directory must be absolute")
            env["DREAM_INFERENCE_LEASE_DIR"] = lease
    return env


def launch(config: LocalEvalConfig, run_dir: Path) -> dict:
    """Create one private run directory and invoke a fresh interpreter explicitly."""
    run_dir = Path(run_dir).absolute()
    run_dir.mkdir(mode=0o700)  # Existing paths are refused, including symlinks.
    root = run_dir.resolve()
    source_before = (_source_hashes(include_skills=True) if config.catalog_mode == "builtin_tools_skills"
                     else _source_hashes())
    env = child_environment(root, config)
    for key in ("HOME", "XDG_CONFIG_HOME", "XDG_CACHE_HOME", "XDG_DATA_HOME", "XDG_RUNTIME_DIR"):
        Path(env[key]).mkdir(mode=0o700)
    (root / "workspace").mkdir(mode=0o700)
    request = json.dumps({"config": asdict(config), "root": str(root)})
    with (root / "process.log").open("xb") as log:
        try:
            completed = subprocess.run(
                [sys.executable, "-c", "from dream.local_harness_eval import child_main; child_main()"],
                input=request.encode(), cwd=root / "workspace", env=env,
                stdout=log, stderr=log, timeout=config.max_wall_seconds + 30, check=False,
            )
        except subprocess.TimeoutExpired:
            # The watchdog kills only this owned Python fixture process. Its
            # socket closes; the separately serving inference engine is untouched.
            return {"status": "watchdog_expired", "passed": False, "run_dir": str(root),
                    "live_endurance_qualified": False}
    report_path = root / "report.json"
    if not report_path.is_file() or report_path.stat().st_size > 2_000_000:
        return {"status": "unavailable", "passed": False, "process_exit": completed.returncode,
                "run_dir": str(root), "live_endurance_qualified": False}
    report = json.loads(report_path.read_text())
    if not isinstance(report, dict):
        raise ValueError("Local fixture returned an invalid report")
    report["process_exit"] = completed.returncode
    source_after = (_source_hashes(include_skills=True) if config.catalog_mode == "builtin_tools_skills"
                    else _source_hashes())
    report["source_sha256"] = source_before
    report["source_consistency"] = {"unchanged": source_before == source_after,
        "scope": "Listed before/after files only; not loaded-byte, dependency or installed-build attestation"}
    report["passed"] = (completed.returncode == 0 and report.get("passed") is True
                        and source_before == source_after)
    report["run_dir"] = str(root)
    return report


async def run_fixture(config: LocalEvalConfig, root: Path, *, transport_factory=None) -> dict:
    """One fixed CSV task; test transport injection is a trusted Python seam."""
    import httpx
    from claude_agent_sdk import SdkMcpTool
    from . import config as settings
    from .core.backends.openai_compat import OpenAICompatBackend
    from .core.endurance_report import build_phase_endurance_report
    from .core.phase_runner import PhaseLimits, PhaseRunner, PhaseSpec
    from .core.profiles import RuntimeProfile
    from .core.providers import Provider
    from .core.task_checkpoints import current, wrap_tool
    from .harness_eval import capture_workspace_manifest, grade_deliverable, load_cases
    from .memory.project import project_key
    from .memory.store import MemoryStore
    from .memory.tasks import TaskStore
    from .tools.context import ToolContext, bind_context, err, ok
    from .tools.native import read_file, write_file

    root = root.resolve()
    if (settings.ROOT != root or Path.home() != root / "home"
            or Path.cwd() != root / "workspace"):
        raise ValueError("Local evaluation requires its fresh isolated child process")
    expected_env = child_environment(root, config)
    if any(os.environ.get(key) != value for key, value in expected_env.items()):
        raise ValueError("Local evaluation environment differs from its pinned isolation policy")
    workspace = root / "workspace"
    fixture = next(case for case in load_cases() if case["id"] == "data-totals")
    (workspace / "sales.csv").write_text(fixture["input"], encoding="utf-8")
    before = capture_workspace_manifest(workspace)
    calls, requests, preflight, refused_selections = [], [], [], []
    started = time.monotonic()
    request_dir = root / "requests"
    request_dir.mkdir(mode=0o700)
    provider = Provider(key="machx", label="Explicit local fixture", kind="openai",
                        base_url=config.endpoint.rstrip("/"), default_api_key="", multimodal=False)
    profile = RuntimeProfile("local-fixture", "compact", 16384, 2048, 0, .10, 1,
                             config.max_wall_seconds, config.max_wall_seconds, False,
                             max_run_tools=64, max_wall_seconds=config.max_wall_seconds, vision=False)

    class CaptureTransport(httpx.AsyncBaseTransport):
        def __init__(self, recipient):
            self.recipient = recipient
            self.inner = transport_factory() if transport_factory else httpx.AsyncHTTPTransport(trust_env=False)

        async def handle_async_request(self, request):
            parsed = urlsplit(str(request.url))
            origin = urlsplit(provider.base_url)
            if (request.method == "GET" and (parsed.scheme, parsed.netloc) == (origin.scheme, origin.netloc)
                    and parsed.path in ("/health", "/props", "/v1/models")):
                if len(preflight) >= 32 or time.monotonic() - started >= config.max_wall_seconds:
                    raise RuntimeError("Local fixture preflight budget exhausted")
                response = await self.inner.handle_async_request(request)
                body = bytearray()
                try:
                    async for chunk in response.stream:
                        if len(body) + len(chunk) > 65536:
                            raise ValueError("Serving metadata exceeds 64 KiB")
                        body.extend(chunk)
                finally:
                    await response.stream.aclose()
                preflight.append({"path": parsed.path, "status": response.status_code,
                                  "sha256": hashlib.sha256(body).hexdigest()})
                return httpx.Response(response.status_code, headers=response.headers, content=bytes(body))
            if (request.method != "POST" or str(request.url) != provider.base_url + "/chat/completions"):
                raise ValueError("Fixture transport refuses an unconfigured route")
            if len(requests) >= config.max_requests or time.monotonic() - started >= config.max_wall_seconds:
                raise RuntimeError("Local fixture request or wall budget exhausted")
            body = await request.aread()
            if len(body) > 131072:
                raise ValueError("Fixture request exceeds 128 KiB")
            payload = json.loads(body)
            if payload.get("model") != config.model:
                raise ValueError("Fixture request changed the pinned model")
            index = len(requests)
            filename = f"{index:03d}-{self.recipient}.json"
            (request_dir / filename).write_bytes(body)
            row = {"recipient": self.recipient, "request": filename,
                   "request_sha256": hashlib.sha256(body).hexdigest(), "status": "inflight",
                   "usage": None, "reported_model": None, "reported_model_conflict": False,
                   "component_tokens": None,
                   "stream_complete": False}
            requests.append(row)
            response = await self.inner.handle_async_request(request)
            inner_stream = response.stream

            class CaptureStream(httpx.AsyncByteStream):
                async def __aiter__(self):
                    captured = bytearray()
                    pending = bytearray()
                    try:
                        async for chunk in inner_stream:
                            if len(captured) + len(chunk) > 1_048_576:
                                raise ValueError("Fixture response exceeds 1 MiB")
                            captured.extend(chunk)
                            lines = chunk.split(b"\n")
                            pending.extend(lines[0])
                            if len(lines) > 1:
                                _observe_sse(row, bytes(pending))
                                pending.clear()
                                for line in lines[1:-1]:
                                    _observe_sse(row, line)
                                pending.extend(lines[-1])
                            yield chunk
                        if pending:
                            _observe_sse(row, bytes(pending))
                        row["status"] = "returned" if response.status_code == 200 else "http_error"
                    finally:
                        row["response_sha256"] = hashlib.sha256(captured).hexdigest()
                        if row["status"] == "inflight":
                            row["status"] = ("returned" if row["stream_complete"] and response.status_code == 200
                                             else "incomplete")

                async def aclose(self):
                    await inner_stream.aclose()

            response.stream = CaptureStream()
            return response

        async def aclose(self):
            await self.inner.aclose()

    class FixtureBackend(OpenAICompatBackend):
        async def _exec_tool(self, name, args, allowed=None, *, described=False):
            # These are backend dispatch hatches, rather than ordinary handlers.
            # Catalog clones cannot close them; the fixture closes them explicitly.
            if name.lower() in {"task", "snip", "fork_verifier_agent"}:
                refused_selections.append({"name": name, "arguments": dict(args),
                                           "reason": "Fixture dispatch control is unavailable"})
                return "Fixture dispatch control is unavailable", True
            return await super()._exec_tool(name, args, allowed, described=described)

        async def connect(self):
            # Explicit fixture connection; no launcher, role scan or capability
            # guess from operator state. Actual request/tool/lease loops are reused.
            self.vitals = False
            self.apply_context_policy()
            self.apply_worker_cap()
            self._settings_notices = []
            self._client = httpx.AsyncClient(base_url=provider.base_url, trust_env=False,
                transport=CaptureTransport(self.recipient), follow_redirects=False,
                timeout=httpx.Timeout(config.max_wall_seconds, connect=5.0))
            try:
                response = await self._client.get(provider.base_url.removesuffix("/v1") + "/health")
                health = response.json() if response.status_code == 200 else {}
                if (not isinstance(health, dict) or any(type(health.get(key)) is not int or health[key] != 0
                                                       for key in ("inflight", "queued", "lanes_active"))):
                    raise ValueError("Serving endpoint is busy or its health is unknown; no inference dispatched")
                self.n_ctx = await self._probe_n_ctx()
                if self.n_ctx is None:
                    raise ValueError("Serving context is unknown; no inference dispatched")
            except BaseException:
                await self._client.aclose()
                raise
            self._configure_tool_images()

    def make_backend(tools, prompt, recipient):
        backend = FixtureBackend(provider=provider, model=config.model, system_prompt=prompt,
            tools=tools, permission_cb=None, temperature=0, subagents=None,
            profile=profile, discovery_mode=config.discovery_mode)
        backend.recipient = recipient
        return backend

    def guarded(tool):
        async def invoke(arguments):
            raw = arguments.get("path")
            allowed = {str(workspace / name) for name in ("sales.csv", "totals.json")}
            coordinator = current()
            if coordinator is not None:
                allowed.add(str(coordinator.journal.path / "progress.md"))
            path = workspace / raw if isinstance(raw, str) else None
            if path is None or str(path) not in allowed or path.is_symlink():
                return err("Fixture tools allow only sales.csv, totals.json and current progress.md")
            if tool.name == "write_file":
                if path == workspace / "sales.csv" or set(arguments) - {"path", "content", "append"}:
                    return err("Fixture input and unconfigured write options are protected")
                text = arguments.get("content")
                if not isinstance(text, str) or len(text.encode()) > 8192:
                    return err("Fixture writes require at most 8 KiB text")
            result = await tool.handler(arguments)
            calls.append({"name": tool.name, "arguments": dict(arguments), "result": result})
            return result
        return wrap_tool(SdkMcpTool(name=tool.name, description=tool.description,
                                   input_schema=tool.input_schema, handler=invoke))

    project = project_key(workspace)
    store = MemoryStore(root / "tasks.db")
    store.project = project
    tasks = TaskStore(store)
    context = ToolContext(None, None, None, session_id="local-fixture", workspace=workspace, tasks=tasks)
    prompt = "Use the supplied real tools. Inspect inputs, save the requested artifact, and verify it."
    lead_tools = [guarded(read_file), guarded(write_file)]
    catalog = {"kind": "fixture", "count": len(lead_tools),
               "allowed": [tool.name for tool in lead_tools], "denied": []}
    skill_rows, skill_index = (), ""
    if config.catalog_mode == "builtin_tools_skills":
        from .local_eval_skills import load_packaged_skills, find_skills, open_skill
        from .tools.installed_skill_tools import skill_find, skill_open
        from .tools.artifact_tools import artifact_read
        from .core import discovery
        skill_rows = load_packaged_skills(_packaged_root())
        if not any(row.name == "data-analysis" for row in skill_rows):
            raise ValueError("Packaged data-analysis workflow is unavailable")
        (root / "skills-snapshot.json").write_text(json.dumps([asdict(row) for row in skill_rows],
                                                              ensure_ascii=False, indent=2), encoding="utf-8")
        lines, omitted = discovery.bounded_lines(
            [discovery.entry_line(row.name, row.description) for row in skill_rows], discovery.SKILL_INDEX_CHARS)
        skill_index = "\nPackaged workflow descriptions:\n" + "\n".join(lines)
        skill_index += f"\n{omitted} additional workflows: skill_find searches names/descriptions by all query words."

        async def find_handler(arguments):
            if set(arguments) - {"query"}:
                return err("Packaged skill search accepts only query")
            try:
                hits = find_skills(skill_rows, arguments.get("query", ""))
            except ValueError as exc:
                return err(str(exc))
            # JSON lines retain exact metadata; each query response is bounded.
            text = json.dumps({"matches": [{"name": row.name, "description": row.description,
                              "source": row.source} for row in hits[:25]], "omitted": max(0, len(hits) - 25)},
                              ensure_ascii=False)
            return ok(text)

        async def open_handler(arguments):
            if set(arguments) != {"name"}:
                return err("Packaged skill open requires only name")
            try:
                return ok(open_skill(skill_rows, arguments["name"]))
            except ValueError as exc:
                return err(str(exc))

        def recorded(original, handler):
            async def invoke(arguments):
                result = await handler(arguments)
                calls.append({"name": original.name, "arguments": dict(arguments), "result": result})
                return result
            return wrap_tool(SdkMcpTool(name=original.name, description=original.description,
                                       input_schema=original.input_schema, handler=invoke))

        lead_tools.extend([recorded(skill_find, find_handler), recorded(skill_open, open_handler),
                           recorded(artifact_read, artifact_read.handler)])
    if config.catalog_mode != "fixture":
        from .local_eval_catalog import build_tool_catalog
        lead_tools, catalog = build_tool_catalog(lead_tools)
        for tool in lead_tools:
            if tool.name not in catalog["allowed"]:
                refusal_handler = tool.handler

                async def record_refusal(arguments, *, name=tool.name, handler=refusal_handler):
                    result = await handler(arguments)
                    refused_selections.append({"name": name, "arguments": dict(arguments),
                                               "result": result})
                    return result
                tool.handler = record_refusal
        prompt += (" This is a builtin-tool discovery fixture. Tools for other tasks are catalogued but "
                   "cannot execute here; only explicitly allowed fixture handlers execute.")
        if skill_rows:
            catalog["kind"], catalog["skills_loaded"] = config.catalog_mode, True
            catalog["skills"] = {"count": len(skill_rows), "indexed": len(lines), "omitted": omitted,
                "sources": [{"name": row.name, "source": row.source, "sha256": row.sha256} for row in skill_rows]}
            catalog["scope"] = "Shipped metadata and packaged SKILL.md snapshots only; no private/plugin roots, supporting files or scripts."
            prompt += (" Use skill_find to discover a workflow for the task, then skill_open its exact name before "
                       "reading inputs. Full workflow text arrives only on request. artifact_read pages managed "
                       "large results from this run. Supporting files, shell and scripts are unavailable: follow "
                       "only workflow steps supported by the guarded fixture read_file/write_file tools.")
        else:
            prompt += " No skill workflow catalog is loaded."
    prompt += skill_index
    (root / "catalog.json").write_text(json.dumps(catalog, indent=2), encoding="utf-8")
    lead = make_backend(lead_tools, prompt, "lead")

    class Worker:
        def __init__(self):
            self.workspace, self.project, self.provider = workspace, project, provider
            self.model, self.profile, self.backend = config.model, profile, lead
            self.tasks, self._tool_context, self._assembled_system_prompt = tasks, context, prompt

        async def ask(self, text):
            with bind_context(context):
                stream = lead.ask(text)
                try:
                    async for event in stream:
                        yield event
                finally:
                    await stream.aclose()

        async def interrupt(self):
            await lead.interrupt()

    def records():
        artifact = workspace / "totals.json"
        raw = artifact.read_text() if artifact.is_file() and artifact.stat().st_size <= 8192 else ""
        try:
            output = json.loads(raw)
        except ValueError:
            output = {}
        if not isinstance(output, dict):
            output = {}
        canonical_calls = []
        for call in calls:
            arguments = dict(call["arguments"])
            raw_path = arguments.get("path")
            if isinstance(raw_path, str) and Path(raw_path).is_absolute():
                try:
                    arguments["path"] = Path(raw_path).relative_to(workspace).as_posix()
                except ValueError:
                    pass
            canonical_calls.append({**call, "arguments": arguments})
        return {"schema_version": 1, "origin": "external-recording", "run_id": "local-fixture",
                "cases": [{"id": "data-totals", "outputs": {**output, "artifact": raw}, "tools": canonical_calls}]}

    def check():
        # Only this host-reserved subtree is excluded. Model tools cannot write
        # it; the backend stores exact context archives there at checked resets.
        after = {name: value for name, value in capture_workspace_manifest(workspace).items()
                 if not name.startswith(".dream/")}
        return (grade_deliverable(records(), case_id="data-totals", workspace_before=before,
                                 workspace_after=after, allowed_outputs={"totals.json"})["passed"]
                and (not skill_rows or skill_selection_checked()))

    def skill_selection_checked():
        found = opened = False
        for call in calls:
            result = call["result"]
            if result.get("is_error") or result.get("isError"):
                continue
            if call["name"] == "skill_find":
                data = json.loads(result["content"][0]["text"])
                found = found or any(row["name"] == "data-analysis" for row in data["matches"])
            elif call["name"] == "skill_open" and found:
                opened = opened or call["arguments"].get("name", "").strip().casefold() == "data-analysis"
            elif call["name"] == "read_file" and Path(call["arguments"]["path"]).name == "sales.csv":
                return opened
        return False

    def reviewer(selected, tools, system, cwd):
        if selected.provider != provider or selected.model != config.model or Path(cwd) != workspace:
            raise ValueError("Local fixture reviewer changed the pinned endpoint/model/workspace")
        return make_backend(tools, system, "reviewer")

    goal = fixture["task"] + " Save the exact totals, rows and excluded fields as totals.json."
    if skill_rows:
        goal += " First discover and open the packaged CSV analysis workflow using skill_find and skill_open."
    version = hashlib.sha256(Path(__file__).read_bytes() + json.dumps(fixture, sort_keys=True).encode()
                             + json.dumps(catalog, sort_keys=True).encode()).hexdigest()
    runner = PhaseRunner(Worker(), (PhaseSpec("csv", goal, "Save correct totals.json; preserve sales.csv.",
        "csv-semantic-check", version, check),), goal=goal, state_dir=root / "schedules",
        limits=PhaseLimits(max_dispatches=8, max_wall_seconds=config.max_wall_seconds),
        evaluator_backend_factory=reviewer)
    result, failure, failure_message = None, None, None
    try:
        await lead.connect()
        with bind_context(context):
            async with asyncio.timeout(config.max_wall_seconds):
                result = await runner.run()
    except Exception as exc:
        failure = type(exc).__name__
        failure_message = str(exc)[:300]
    finally:
        await lead.disconnect()
    report = None
    try:
        if result is not None and result.schedule_id:
            report = build_phase_endurance_report(root / "schedules", result.schedule_id, task_lookup=tasks.get)
    finally:
        store.close()
    if failure_message is None and result is not None and result.status != "done":
        failure_message = result.message[:300] if isinstance(result.message, str) else None
    identity_matches = all(row["reported_model"] in (None, config.model)
                           and not row["reported_model_conflict"] for row in requests)
    discovery_report = lead.discovery_report(prompt_components={
        "base_prompt": prompt, "stable_sections": "", "mcp_section": "",
        "learned_skill_index": "", "installed_skill_index": "", "task_guidance": ""})
    return {"schema_version": 1, "status": failure or (result.status if result else "unavailable"),
            "passed": result is not None and result.status == "done" and failure is None and identity_matches,
            "requested_model": config.model, "discovery_mode": config.discovery_mode,
            "catalog": catalog, "discovery_report": discovery_report,
            "skill_selection": {"required": bool(skill_rows), "checked": skill_selection_checked() if skill_rows else None,
                                "scope": "Observed metadata search/open before CSV read; not full workflow compliance or model quality"},
            "refused_selections": refused_selections,
            "model_identity": {"no_reported_mismatch": identity_matches,
                               "all_requests_report_model": bool(requests) and all(row["reported_model"] is not None for row in requests),
                               "independently_verified": False},
            "fixture": "data-totals", "elapsed_seconds": time.monotonic() - started,
            "failure_message": failure_message, "preflight": preflight,
            "synthetic_transport": transport_factory is not None, "requests": requests,
            "tool_calls": calls, "grading_paths": "workspace-relative; original arguments retained in tool_calls",
            "context_policy": {"assumed_context": profile.assumed_context, "served_context": lead.n_ctx},
            "ledger_evidence": report, "serving_build_independently_verified": None,
            "live_endurance_qualified": False, "workspace_exclusion": ".dream/ host archives only"}


def child_main():
    request = json.loads(sys.stdin.buffer.read(16385))
    config = LocalEvalConfig(**request["config"])
    root = Path(request["root"])
    try:
        report = asyncio.run(run_fixture(config, root))
    except Exception as exc:
        report = {"status": type(exc).__name__, "passed": False, "live_endurance_qualified": False}
    with (root / "report.json").open("x", encoding="utf-8") as output:
        json.dump(report, output, indent=2)
    raise SystemExit(0 if report["passed"] else 1)
