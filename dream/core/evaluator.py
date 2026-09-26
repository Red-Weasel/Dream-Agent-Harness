"""Provider-selectable reviews with independent conversations and bound read tools.

No Engine is constructed, no global ToolContext is installed, and no provider fallback
is attempted. A Claude review runs like the main-model Claude path (DREAM-137) and reads
the bound session's Dream tool server from the ToolContext; CLI reviews run like the
main-model CLI path (DREAM-136) with a bounded textual read protocol bound to the same
scoped file handlers.
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
import hashlib
import os
from pathlib import Path
import stat
from typing import Any

from claude_agent_sdk import SdkMcpTool

from .backends.base import Event, content_to_text
from .backends.openai_compat import OpenAICompatBackend
from .providers import Provider, get_provider
from .profiles import RuntimeProfile, resolve_profile
from .review_usage import attributed_meter


@dataclass(frozen=True)
class ReviewSettings:
    provider: Provider
    model: str | None
    timeout: float = 120.0
    profile: RuntimeProfile | None = None
    runtime_meter: Any = field(default=None, repr=False, compare=False)
    # Dream's permission mode; a CLI or Claude reviewer's tool posture follows it (DREAM-136, DREAM-137).
    mode: str | None = None

    @classmethod
    def resolve(cls, engine, *, provider=None, model=None, timeout=None):
        worker = getattr(engine, 'provider', 'anthropic')
        worker = get_provider(worker) if isinstance(worker, str) else worker
        selected = provider or os.environ.get('DREAM_EVALUATOR_PROVIDER') or worker
        selected = get_provider(selected) if isinstance(selected, str) else selected
        chosen_model = model or os.environ.get('DREAM_EVALUATOR_MODEL')
        if not chosen_model:
            chosen_model = (getattr(engine, 'model', None) if selected.key == worker.key
                            else selected.default_model)
        seconds = float(timeout if timeout is not None else os.environ.get('DREAM_EVALUATOR_TIMEOUT', '120'))
        if not 0 < seconds <= 3600:
            raise ValueError('Evaluator timeout must be between 0 and 3600 seconds')
        profile = (getattr(engine, 'profile', None) if selected.key == worker.key
                   and chosen_model == getattr(engine, 'model', None) else None)
        mode_getter = getattr(engine, '_mode_getter', None)
        return cls(selected, chosen_model, seconds, profile or resolve_profile(selected, model=chosen_model),
                   getattr(engine, 'runtime_meter', None), mode_getter() if mode_getter else None)


class ScopedReader:
    """Regular-file reads beneath fixed roots, without following any symlink.

    Descriptor-relative traversal prevents a concurrently replaced parent symlink
    from escaping the scope. Reads and enumeration are bounded; no shell is used.
    """
    def __init__(self, *roots: Path):
        self.roots = tuple(dict.fromkeys(Path(root).resolve() for root in roots))
        self.inspected: dict[str, str] = {}

    def _open(self, value: str) -> tuple[int, Path]:
        path = Path(os.path.abspath(self.roots[0] / value))
        for root in self.roots:
            try:
                parts = path.relative_to(root).parts
            except ValueError:
                continue
            if not parts:
                raise ValueError('Expected a regular file')
            directory = os.open(root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
            try:
                for part in parts[:-1]:
                    next_fd = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=directory)
                    os.close(directory)
                    directory = next_fd
                fd = os.open(parts[-1], os.O_RDONLY | os.O_NONBLOCK | os.O_NOFOLLOW, dir_fd=directory)
                return fd, path
            finally:
                os.close(directory)
        raise ValueError('Path is outside the review roots')

    def read(self, value: str) -> tuple[str, str]:
        fd, path = self._open(value)
        with os.fdopen(fd, 'rb') as source:
            info = os.fstat(source.fileno())
            if not stat.S_ISREG(info.st_mode) or info.st_size > 1_048_576:
                raise ValueError('Review requires a regular file of at most 1 MiB')
            body = source.read(1_048_577)
        if len(body) > 1_048_576:
            raise ValueError('Review file grew beyond the size limit')
        sha = hashlib.sha256(body).hexdigest()
        previous = self.inspected.get(str(path))
        if previous is not None and previous != sha:
            raise ValueError('Artifact changed during review')
        self.inspected[str(path)] = sha
        return body.decode('utf-8', 'replace'), sha

    def unchanged(self) -> bool:
        try:
            for path in list(self.inspected):
                self.read(path)
            return True
        except (OSError, ValueError):
            return False

    def tools(self) -> list[SdkMcpTool]:
        async def read_file(args):
            try:
                text, sha = self.read(str(args['path']))
                start = max(1, int(args.get('start_line', 1)))
                excerpt = '\n'.join(f'{i}: {line}' for i, line in enumerate(text.splitlines(), 1)
                                    if start <= i < start + 200)[:24000]
                return {'content': [{'type': 'text', 'text': f'SHA256: {sha}\n{excerpt}'}]}
            except (OSError, ValueError, KeyError, TypeError) as exc:
                return {'is_error': True, 'content': [{'type': 'text', 'text': str(exc)}]}

        async def list_files(args):
            names, visited = [], 0
            pattern = str(args.get('contains', ''))
            for root in self.roots:
                for folder, dirs, files in os.walk(root, followlinks=False):
                    dirs[:] = sorted(d for d in dirs if not d.startswith('.') and d not in ('node_modules', '__pycache__'))
                    visited += 1
                    for name in sorted(files):
                        path = str(Path(folder) / name)
                        if pattern in path:
                            names.append(path)
                        if len(names) >= 200:
                            break
                    if len(names) >= 200 or visited >= 1000:
                        break
                if len(names) >= 200 or visited >= 1000:
                    break
            return {'content': [{'type': 'text', 'text': '\n'.join(names) + '\n(bounded listing; narrow with contains)'}]}

        return [SdkMcpTool(name='read_file', description='Read numbered lines of a review-scoped file; no writes.',
                           input_schema={'type': 'object', 'properties': {'path': {'type': 'string'}, 'start_line': {'type': 'integer'}}, 'required': ['path']}, handler=read_file),
                SdkMcpTool(name='list_files', description='List up to 200 review-scoped filenames.',
                           input_schema={'type': 'object', 'properties': {'contains': {'type': 'string'}}}, handler=list_files)]


class IsolatedOpenAIBackend(OpenAICompatBackend):
    """Disable the ambient-memory and delegation hooks on a bare backend."""
    def __init__(self, **kwargs):
        if kwargs.get('profile') is None:
            kwargs['profile'] = resolve_profile(kwargs['provider'], model=kwargs.get('model'))
        super().__init__(**kwargs)

    def _note_elided(self):
        return None

    def _tool_uses(self):
        return None

    def _pinned_tools(self):
        return frozenset(t.name for t in self.tools)

    def _request_tools(self):
        return self.tool_schemas

    async def _exec_tool(self, name, args, allowed=None):
        tool = self.tools_by_name.get(name.lower())
        if tool is None:
            return 'Denied: only the explicitly bound review tools are available.', True
        try:
            if self.runtime_meter is not None:
                self.runtime_meter.before_tool(name)
            result = await tool.handler(args)
            return content_to_text(result.get('content')), bool(result.get('is_error'))
        except Exception as exc:
            return f'Read unavailable: {type(exc).__name__}: {exc}', True

    async def _run_filer(self, turn_text):
        return []

    async def _run_verifier_sweep(self):
        return []


class SDKReviewBackend:
    def __init__(self, settings, tools, system_prompt, cwd, sdk_query=None):
        self.settings, self.tools, self.system_prompt, self.cwd = settings, tools, system_prompt, cwd
        self.sdk_query = sdk_query
        self.runtime_meter = attributed_meter(settings.provider, scope='evaluator', meter=settings.runtime_meter)

    @property
    def provenance(self):
        from .backends.anthropic import _session_tools, consult_provenance
        return consult_provenance(str(self.cwd), self.settings.mode, dream_tools=bool(_session_tools()))

    async def connect(self):
        pass

    async def disconnect(self):
        pass

    async def ask(self, prompt):
        import json
        import types
        from claude_agent_sdk import (AssistantMessage, ResultMessage,
                                      TextBlock, create_sdk_mcp_server, query)
        from .backends.anthropic import consult_options
        names = ['mcp__review__' + tool.name for tool in self.tools]
        # Runs like the main Claude path (DREAM-137); the bound review reader stays
        # pre-approved, since the loop downgrades a PASS when a file it read changes.
        servers = {'review': create_sdk_mcp_server(name='review', tools=self.tools)} if self.tools else None
        opts = consult_options(system_prompt=self.system_prompt, cwd=str(self.cwd), mode=self.settings.mode,
                               model=self.settings.model, effort=None, extra_servers=servers, extra_allowed=names)
        if self.runtime_meter:
            self.runtime_meter.check()
        # Keep callback-based read permissions active with streaming input.
        async def messages():
            yield {'type': 'user', 'message': {'role': 'user', 'content': prompt},
                   'parent_tool_use_id': None, 'session_id': ''}

        stream = (self.sdk_query or query)(prompt=messages(), options=opts)
        failure = None
        received = False
        cancellation = None
        read_failure = None
        read_cancels = []

        @types.coroutine
        def observe(awaitable, cancellations):
            iterator = awaitable.__await__()
            try:
                value = next(iterator)
                while True:
                    pending = None
                    try:
                        sent = yield value
                    except BaseException as exc:
                        pending = exc
                    # Forward outside the handler so the bridge cannot replace
                    # a retained provider error's context with this exception.
                    if isinstance(pending, GeneratorExit):
                        close = getattr(iterator, 'close', None)
                        if close is not None:
                            close()
                        raise pending
                    if pending is not None:
                        if isinstance(pending, asyncio.CancelledError):
                            cancellations[:] = [pending]
                        throw = getattr(iterator, 'throw', None)
                        if throw is None:
                            raise pending
                        value = throw(pending)
                    else:
                        value = next(iterator) if sent is None else iterator.send(sent)
            except StopIteration as finished:
                return finished.value

        def quoted(value):
            return json.dumps(str(value)[:160], ensure_ascii=True)

        def cancelled(exc, injected):
            if isinstance(exc, asyncio.CancelledError):
                return exc
            # Recover only the latest cancellation actually forwarded during
            # this await, never history inferred from task counts or text.
            if injected:
                seen = set()
                while exc is not None and id(exc) not in seen:
                    seen.add(id(exc))
                    if exc is injected[0]:
                        return exc
                    exc = exc.__context__
            return None

        def note(cancel, exc):
            if exc is not cancel:
                cancel.add_note(f'Reviewer cleanup failed: {quoted(type(exc).__name__)}: {quoted(exc)}')

        try:
            iterator = aiter(stream)
            while True:
                read_cancels = []
                try:
                    message = await observe(anext(iterator), read_cancels)
                except StopAsyncIteration:
                    break
                # A successful read ends its witness before consumer-facing yields.
                read_cancels = []
                if getattr(message, 'parent_tool_use_id', None):
                    continue  # a sub-agent's own output, suppressed as on the main path
                if isinstance(message, AssistantMessage):
                    yield Event('assistant_done', ''.join(block.text for block in message.content if isinstance(block, TextBlock)))
                elif isinstance(message, ResultMessage):
                    if received:
                        failure = failure or 'Reviewer returned duplicate terminal results'
                    else:
                        received = True
                        if self.runtime_meter and isinstance(getattr(message, 'usage', None), dict):
                            self.runtime_meter.usage(message.usage)
                        reason = getattr(message, 'terminal_reason', None)
                        if message.is_error or message.subtype != 'success' or reason not in (None, 'completed'):
                            failure = (f'Reviewer failed: subtype={quoted(message.subtype)}, '
                                       f'terminal_reason={quoted(reason)}')
                    # Exhaust the public SDK iterator so its nested client
                    # closes before returning a verdict or reporting failure.
        except BaseException as exc:
            # Settle close outside an active read exception: rethrowing it here
            # can overwrite a cancellation's context inside a close finalizer.
            read_failure = exc
            cancellation = cancelled(exc, read_cancels)
            if cancellation is not None:
                note(cancellation, exc)
        finally:
            close_cancels = []
            try:
                await observe(stream.aclose(), close_cancels)
            except BaseException as exc:
                primary = cancellation if cancellation is not None else cancelled(exc, close_cancels)
                if primary is not None:
                    note(primary, exc)
                    raise primary
                raise
        if read_failure is not None:
            raise cancellation if cancellation is not None else read_failure
        if not received:
            failure = 'Reviewer missing terminal result'
        if failure:
            yield Event('error', failure)


def review_backend(settings, tools, system_prompt, cwd, *, sdk_query=None):
    if settings.provider.kind == 'openai':
        backend = IsolatedOpenAIBackend(provider=settings.provider, model=settings.model or 'default',
                                     tools=tools, system_prompt=system_prompt, permission_cb=None,
                                     subagents=None, temperature=0.0, profile=settings.profile)
        backend.runtime_meter = attributed_meter(settings.provider, scope='evaluator', meter=settings.runtime_meter)
        return backend
    if settings.provider.kind == 'anthropic':
        return SDKReviewBackend(settings, tools, system_prompt, cwd, sdk_query)
    if settings.provider.kind == 'cli':
        from .cli_review import CLIReviewBackend
        return CLIReviewBackend(settings, tools, system_prompt, cwd)
    raise ValueError(f'{settings.provider.key} has no isolated reviewer contract')


async def collect_review(backend, prompt: str, timeout: float) -> str:
    async def collect():
        parts = []
        try:
            await backend.connect()
            async for event in backend.ask(prompt):
                if event.kind == 'error' or (event.kind == 'result' and isinstance(event.data, dict) and event.data.get('is_error')):
                    raise RuntimeError(f'Reviewer failed: {event.data}')
                if event.kind == 'assistant_done' and event.data:
                    parts.append(str(event.data))
            text = '\n'.join(parts).strip()
            if not text:
                raise ValueError('Reviewer returned no assessment')
            return text
        finally:
            await asyncio.wait_for(backend.disconnect(), timeout=5)
    return await asyncio.wait_for(collect(), timeout=timeout)
