"""Dream MoE — the council: config + one-shot advisor consultations.

An orchestrator provider runs a normal Dream session and can poll a set of *advisors*
— other frontier engines — for an independent take. Each consultation is a single
headless run on that advisor's provider. A CLI advisor runs like the main-model CLI
path (DREAM-136): the owner's CLI configuration, the Dream workspace as cwd, its own
tools, sandboxed by Dream's permission mode. A Claude advisor runs like Claude Code in
VS Code (DREAM-140): the owner's Claude settings, Claude Code's native tools, a
permission mode mapped from Dream's. Their provenance is retained with the answer.
The three per-provider-kind invokers are separate module-level functions so the
council's fan-out and failure-tolerance can be exercised without spawning a CLI or
touching the network (tests monkeypatch the invokers).
"""

from __future__ import annotations

import asyncio
import json
from dataclasses import asdict, dataclass, field
import os
import stat

from .. import config
from .evaluator import IsolatedOpenAIBackend as OpenAICompatBackend
from .council_evidence import assess_legal, prepare_sources
from .run_state import atomic_write
from .providers import Provider, get_provider
from .profiles import resolve_profile
from .review_usage import attributed_meter

CONFIG_PATH = config.VAR_DIR / "moe.json"

# Framing every advisor is booted with. CLI and Claude advisors run with their own
# tools under Dream's permission mode (DREAM-136, DREAM-140); the framing does not
# promise otherwise.
ADVISOR_SYSTEM = (
    "You are being consulted by another AI agent as an independent advisor. Give your "
    "own honest, concise take on the question it puts to you — your real reasoning, not "
    "a rubber stamp. Where your tools allow, you may inspect the workspace's files and "
    "images and search the web to ground your answer, and act only within Dream's "
    "permission mode. Disagree when you disagree, name the risks or trade-offs the "
    "asker may have missed. Attribute claims to inspectable sources, explicitly mark "
    "unsupported legal claims unverified, and preserve dissent. Agreement is never proof."
)


@dataclass(frozen=True)
class MoeConfig:
    """A persisted council: which provider orchestrates and which it may consult."""

    orchestrator: str        # provider key: codex | grok | gemini | anthropic | machx
    advisors: list[str]      # provider keys consulted
    max_concurrency: int = 3
    timeout_seconds: float = 90.0
    legal_review: bool = False
    advisor_models: dict[str, str] = field(default_factory=dict)
    orchestrator_effort: str | None = None
    advisor_efforts: dict[str, str] = field(default_factory=dict)


def _read_config() -> object:
    """The Council file's JSON, read the way core/profiles.read_settings reads a settings file (DREAM-148, gate
    round 1): opened without blocking, so a FIFO nobody writes to cannot hang a session start; a regular file only;
    at most MAX_SETTINGS_BYTES. A link is followed, as it always was. FileNotFoundError when there is no file;
    ValueError (or RecursionError, from absurdly deep nesting) when it cannot be used."""
    from .profiles import MAX_SETTINGS_BYTES
    fd = os.open(CONFIG_PATH, os.O_RDONLY | os.O_NONBLOCK)
    try:
        info = os.fstat(fd)     # before os.fdopen, which refuses a directory with its own error
        if not stat.S_ISREG(info.st_mode):
            kind = next((name for test, name in ((stat.S_ISFIFO, "a FIFO"), (stat.S_ISDIR, "a directory"),
                                                 (stat.S_ISCHR, "a character device"), (stat.S_ISBLK, "a block device"))
                         if test(info.st_mode)), "a special file")
            raise ValueError(f"it is not a regular file ({kind})")
    except BaseException:
        os.close(fd)
        raise
    with os.fdopen(fd, "rb") as source:
        data = source.read(MAX_SETTINGS_BYTES + 1) if info.st_size <= MAX_SETTINGS_BYTES else b""
    if info.st_size > MAX_SETTINGS_BYTES or len(data) > MAX_SETTINGS_BYTES:
        raise ValueError(f"it is larger than {MAX_SETTINGS_BYTES:,} bytes")
    return json.loads(data.decode("utf-8"))


def load_config() -> MoeConfig | None:
    """The saved council, or ``None`` if there is none / the file is unreadable or unusable (config_problem says
    why; both read through _read_config, so they always agree)."""
    try:
        data = _read_config()
        from .council_config import parse_config
        return parse_config(data)
    except (FileNotFoundError, OSError, ValueError, KeyError, TypeError, RecursionError):
        return None


def config_problem() -> tuple[str | None, str] | None:
    """What makes the saved Council file unusable -- (the entry at fault, the rule it breaks), e.g. ("advisors",
    "Advisors must be unique"), or (None, what is wrong with the file itself) -- or None when it is usable or absent
    (DREAM-148). load_config() treats such a file as no saved Council and nothing rewrites it; this is what Dream
    says instead of nothing. Read only; never raises for the file's content; bounded and printable (profiles.shown)."""
    from .profiles import shown
    try:
        data = _read_config()
    except FileNotFoundError:
        return None
    except UnicodeDecodeError:
        return None, "it is not UTF-8 text"
    except json.JSONDecodeError as exc:
        return None, f"it is not valid JSON (line {exc.lineno}, column {exc.colno}: {exc.msg})"
    except RecursionError:
        return None, "it is not JSON Dream can read (nested too deeply)"
    except OSError as exc:
        return None, f"it could not be read ({exc.strerror or type(exc).__name__})"
    except ValueError as exc:       # a special or oversized file (_read_config), or a number past Python's digit limit
        return None, shown(str(exc), 300)
    from .council_config import parse_config
    try:
        parse_config(data)
    except ValueError as exc:
        key = getattr(exc, "key", None)
        return (shown(key, 200) if key else None), shown(str(exc), 300)
    except (KeyError, TypeError, RecursionError) as exc:
        return None, f"it could not be checked ({type(exc).__name__})"
    return None


def config_note(*, entry_only: bool = False) -> str | None:
    """The sentence every surface shows for an unusable Council file -- the session-start note, `dream settings
    check` and /settings check, the Settings tab's Council section -- naming the file and the entry; None when the
    file is usable or absent. `entry_only` (the Settings tab, which names an entry and never a value from a file)
    leaves out the rule's own wording, which may quote the value."""
    problem = config_problem()
    if problem is None:
        return None
    from .profiles import shown_path
    entry, reason = problem
    what = (f"the {entry} entry is not valid" if entry_only else f"{entry}: {reason}") if entry else reason
    return (f"the Council settings file {shown_path(CONFIG_PATH)} could not be used, so the saved Council is off -- "
            f"{what}. Dream never rewrites it; repair it by hand")


def save_config(cfg: MoeConfig) -> None:
    """Persist ``{orchestrator, advisors}`` (creating the var dir if needed)."""
    CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    atomic_write(CONFIG_PATH, json.dumps(asdict(cfg), indent=2) + '\n')


# --- one-shot advisor calls, one per provider kind ---------------------------


def _context_meter(provider, meter=None):
    """The consultation's meter: `meter` when a caller gives one (the Frontier loop gives each call its own, DREAM-218),
    else the bound run's."""
    return attributed_meter(provider, scope='council', meter=meter)


async def _run_backend(backend, prompt: str) -> str:
    """Connect, collect the assistant text across one ``ask()``, disconnect.

    Backends report transport failures as ``error`` events instead of raising (an
    OpenAI-compat advisor surfaces every HTTP failure that way), so those are kept
    and appended: an advisor that never answered must not come back as silent
    emptiness the council could read as agreement."""
    try:
        await backend.connect()
        parts: list[str] = []
        errors: list[str] = []
        async for ev in backend.ask(prompt):
            if ev.kind == "assistant_done" and ev.data:
                parts.append(str(ev.data))
            elif ev.kind == "error" and ev.data:
                errors.append(str(ev.data))
            elif ev.kind == "result" and isinstance(ev.data, dict) and ev.data.get('is_error'):
                errors.append(str(ev.data))
        text = "\n".join(parts).strip()
        if not errors:
            return text or '[unavailable — advisor returned no answer]'
        # The event already carries the provider label, so the note reads as
        # "[failed — MachX HTTP 500: …]" next to the advisor's own label.
        note = f"[failed — {'; '.join(errors)}]"
        return f"{text}\n\n{note}" if text else note
    finally:
        await asyncio.wait_for(backend.disconnect(), timeout=5)


async def _consult_cli(provider: Provider, prompt: str, *, cwd: str | None = None, model=None, effort=None,
                       mode: str | None = None, system: str | None = None, isolate: bool = False, meter=None) -> str:
    from .cli_review import CLIConsultation, codex_isolation, CODEX_SIGN_IN, runner_env
    consultation = CLIConsultation(provider, model, runtime_meter=_context_meter(provider, meter), cwd=cwd, mode=mode)
    try:
        consultation.prepare()
        if effort is not None:
            from .council_config import validate_effort
            validate_effort(provider.key, effort, model)
            # Codex exec accepts per-invocation configuration before stdin '-'.
            consultation.argv[-1:-1] = ['-c', 'model_reasoning_effort=' + json.dumps(effort)]
        if isolate:
            consultation.argv[-1:-1] = await codex_isolation(consultation.executable, str(consultation.cwd))
            consultation.env = runner_env(CODEX_SIGN_IN)
            consultation.provenance.update(
                isolation="sleepwalk: the owner's plugins, apps, hooks, skills list, project AGENTS.md and MCP servers off",
                configuration="the owner's CLI home and sign-in; config.toml MCP servers disabled, checked with codex mcp list",
                tool_scope="the CLI's own tools under its read-only sandbox, no MCP servers")
        return await consultation.run((system or ADVISOR_SYSTEM) + '\n\n' + prompt)
    finally:
        await consultation._stop()
        consultation.close()


async def _consult_anthropic(provider: Provider, prompt: str, *, cwd: str | None = None, model=None, effort=None,
                             mode: str | None = None, system: str | None = None, isolate: bool = False, meter=None,
                             options=None) -> str:
    """A Claude Agent SDK consultation that runs like Claude Code in VS Code (DREAM-140):
    the owner's settings, native tools, multiple turns, a permission mode mapped from ``mode``
    (none runs as ask). ``options`` replaces those SDK options whole (the Frontier loop's lean call, DREAM-218);
    the stream, receipt and cancellation handling below are the same either way."""
    import types
    from claude_agent_sdk import (
        AssistantMessage,
        ResultMessage,
        TextBlock,
        query,
    )
    from .backends.anthropic import claude_code_options

    opts = options if options is not None else claude_code_options(
        system_prompt=system or ADVISOR_SYSTEM, cwd=cwd or str(config.ROOT), mode=mode,
        model=model or provider.default_model, effort=effort, owner_settings=not isolate)
    texts: list[str] = []
    meter = _context_meter(provider, meter)
    if meter:
        meter.check()
    # Permission callbacks require the SDK's streaming input contract.
    async def messages():
        yield {'type': 'user', 'message': {'role': 'user', 'content': prompt},
               'parent_tool_use_id': None, 'session_id': ''}

    stream = query(prompt=messages(), options=opts)
    failure = None
    result_seen = False
    cancellation = None
    read_failure = None
    reading_cancels = []

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
                # Forward outside the handler to preserve provider contexts.
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

    def cancelled(exc, injected):
        if isinstance(exc, asyncio.CancelledError):
            return exc
        if injected:
            seen = set()
            while exc is not None and id(exc) not in seen:
                seen.add(id(exc))
                if exc is injected[0]:
                    return exc
                exc = exc.__context__
        return None

    try:
        iterator = aiter(stream)
        while True:
            reading_cancels = []
            try:
                msg = await observe(anext(iterator), reading_cancels)
            except StopAsyncIteration:
                break
            reading_cancels = []
            if getattr(msg, 'parent_tool_use_id', None):
                continue  # a sub-agent's own output, suppressed as on the main path
            if isinstance(msg, AssistantMessage):
                # Text on either side of a tool call stays separated, as for CLI advisors.
                part = ''.join(block.text for block in msg.content if isinstance(block, TextBlock)).strip()
                if part:
                    texts.append(part)
            elif isinstance(msg, ResultMessage):
                if result_seen:
                    failure = failure or 'Advisor returned duplicate ResultMessage receipts'
                    continue
                result_seen = True
                if meter and isinstance(getattr(msg, 'usage', None), dict):
                    meter.usage(msg.usage)
                if msg.is_error:
                    failure = 'Advisor failed: subtype=' + json.dumps(str(msg.subtype)[:80])
                elif msg.subtype != 'success':
                    failure = 'Advisor incomplete: subtype=' + json.dumps(str(msg.subtype)[:80])
                elif getattr(msg, 'terminal_reason', None) not in (None, 'completed'):
                    failure = 'Advisor incomplete: terminal_reason=' + json.dumps(str(msg.terminal_reason)[:80])
                # Drain through EOF: public SDK query() does not immediately
                # close its nested client generator when a consumer breaks.
    except BaseException as exc:
        # Close outside an active read exception, which could otherwise replace
        # current cancellation context inside the provider's close finalizer.
        read_failure = exc
        cancellation = cancelled(exc, reading_cancels)
        if cancellation is not None and cancellation is not exc:
            cancellation.add_note('Advisor stream failed during cancellation: ' +
                                  json.dumps((type(exc).__name__ + ': ' + str(exc))[:160]))
    finally:
        # The SDK stream owns AnyIO cancellation scopes. Close it in the same
        # task that iterated it, while retaining a bounded cleanup deadline.
        closing_cancels = []
        close_failure = None
        try:
            async with asyncio.timeout(5):
                try:
                    await observe(stream.aclose(), closing_cancels)
                except (Exception, asyncio.CancelledError) as exc:
                    close_failure = exc
                    current = cancelled(exc, closing_cancels)
                    if current is not None and current is not exc:
                        if cancellation is None:
                            current.add_note('Advisor cleanup failed during cancellation: ' +
                                             json.dumps((type(exc).__name__ + ': ' + str(exc))[:160]))
                        # Recover inside timeout so its own injected cancellation
                        # is settled and converted to its ordinary deadline error.
                        raise current from None
                    raise
        except (Exception, asyncio.CancelledError) as exc:
            if cancellation is None:
                raise
            secondary = close_failure if isinstance(exc, asyncio.CancelledError) and close_failure is not None else exc
            # Cleanup must not turn an already cancelled call into an answer.
            cancellation.add_note('Advisor cleanup failed during cancellation: ' +
                                  json.dumps((type(secondary).__name__ + ': ' + str(secondary))[:160]))
            raise cancellation from None
    if read_failure is not None:
        raise cancellation if cancellation is not None else read_failure
    if not result_seen:
        failure = 'Advisor incomplete: missing ResultMessage receipt'
    if failure:
        raise RuntimeError(failure)
    return '\n\n'.join(texts) or '[unavailable — advisor returned no answer]'


async def _consult_openai(provider: Provider, prompt: str, *, cwd: str | None = None, model=None, effort=None,
                          system: str | None = None, meter=None) -> str:
    """One-shot call against an OpenAI-compatible provider, no tools."""
    selected_model = model or provider.default_model or 'default'
    backend = OpenAICompatBackend(
        provider=provider,
        model=selected_model,
        profile=resolve_profile(provider, model=selected_model),
        system_prompt=system or ADVISOR_SYSTEM,
        tools=[],
        permission_cb=None,
    )
    if effort is not None:
        backend.set_effort(effort)
    meter = _context_meter(provider, meter)
    if meter is not None:
        backend.runtime_meter = meter
    return await _run_backend(backend, prompt)


async def consult_advisor(
    provider_key: str, question: str, context: str = "", *, cwd: str | None = None,
    model: str | None = None, timeout: float | None = None, effort: str | None = None,
    mode: str | None = None, system: str | None = None, isolate: bool = False,
) -> str:
    """Ask one advisor for its take. Dispatches by provider kind. Never raises —
    any failure comes back as a short ``[label: unavailable — reason]`` string.

    The invoker is looked up by bare name (not a captured dict) so tests can
    monkeypatch ``_consult_cli`` / ``_consult_anthropic`` / ``_consult_openai``.
    ``mode`` is Dream's permission mode; CLI and Claude advisors take it (their tool posture).
    ``system`` replaces the advisor persona (Sleepwalk's runs, DREAM-156); None keeps it.
    ``isolate`` (Sleepwalk, DREAM-157) runs without the owner's MCP servers, plugins, hooks and project instructions,
    and refuses a runner that cannot (see can_isolate)."""
    label = provider_key
    try:
        provider = get_provider(provider_key)
        label = provider.label
        prompt = f"{context}\n\n{question}" if context else question
        seconds = float(timeout if timeout is not None else os.environ.get('DREAM_COUNCIL_TIMEOUT', '90'))
        if not 0 < seconds <= 3600:
            raise ValueError('Advisor timeout must be between 0 and 3600 seconds')
        kwargs = {'cwd': cwd}
        if model is not None:
            kwargs['model'] = model
        if effort is not None:
            from .council_config import validate_effort
            validate_effort(provider_key, effort, model)
            kwargs['effort'] = effort
        if mode is not None and provider.kind in ('cli', 'anthropic'):
            kwargs['mode'] = mode
        if system is not None:
            kwargs['system'] = system
        if isolate:
            if not can_isolate(provider_key):
                raise ValueError(f"{label} cannot run without the owner's own MCP servers and settings, "
                                 "so an isolated run is refused; choose Codex, Claude or an HTTP runner")
            if provider.kind in ('cli', 'anthropic'):     # an HTTP runner has no local tools at all
                kwargs['isolate'] = True
        invoker = {'cli': _consult_cli, 'anthropic': _consult_anthropic, 'openai': _consult_openai}.get(provider.kind)
        if invoker is None:
            return f"[{label}: unavailable — unknown provider kind '{provider.kind}']"
        return await asyncio.wait_for(invoker(provider, prompt, **kwargs), timeout=seconds)
    except Exception as e:  # noqa: BLE001 — an advisor must never sink the caller
        return f"[{label}: unavailable — {type(e).__name__}: {e}]"


def can_isolate(provider_key: str) -> bool:
    """Whether a run can leave out the owner's tools and settings: Codex (flags, checked by `codex mcp list`),
    Claude (no setting sources) and HTTP runners (no local tools). The Grok and Gemini CLIs have no such switch."""
    return get_provider(provider_key).kind != 'cli' or provider_key == 'codex'


def _label_for(provider_key: str) -> str:
    try:
        return get_provider(provider_key).label
    except Exception:  # noqa: BLE001
        return provider_key


async def council(
    advisors: list[str], question: str, context: str = "", *, cwd: str | None = None,
    max_concurrency: int | None = None, timeout: float | None = None,
    models: dict[str, str] | None = None, efforts: dict[str, str] | None = None, legal: bool = False, sources: list[dict] | None = None,
    mode: str | None = None,
) -> list[dict]:
    """Bounded independent consultations; preserve every answer and its dissent.

    Legal sources are explicit local artifacts, inspected before fan-out. Advisors
    never see other advisors' answers. No vote count is presented as verification.
    """
    limit = int(max_concurrency if max_concurrency is not None else os.environ.get('DREAM_COUNCIL_CONCURRENCY', '3'))
    seconds = float(timeout if timeout is not None else os.environ.get('DREAM_COUNCIL_TIMEOUT', '90'))
    if not 1 <= limit <= 32 or not 0 < seconds <= 3600 or len(advisors) > 32:
        raise ValueError('Council requires 1–32 concurrent calls, at most 32 advisors, and a bounded timeout')
    inspected = {}
    if legal:
        inspected, contract = prepare_sources(sources, cwd or str(config.ROOT))
        context = context + '\n\n' + contract
    semaphore = asyncio.Semaphore(limit)
    async def one(key):
        async with semaphore:
            try:
                kwargs = {'cwd': cwd, 'timeout': seconds}
                if models and key in models:
                    kwargs['model'] = models[key]
                if efforts and key in efforts:
                    kwargs['effort'] = efforts[key]
                if mode is not None:
                    kwargs['mode'] = mode
                answer = await asyncio.wait_for(consult_advisor(key, question, context, **kwargs), timeout=seconds + 6)
            except Exception as exc:
                answer = f'[{_label_for(key)}: unavailable — {type(exc).__name__}: {exc}]'
            result = {'advisor': key, 'label': _label_for(key), 'answer': answer,
                      'model': (models or {}).get(key), 'effort': (efforts or {}).get(key), 'independent': True,
                      'verification': 'unverified', 'consensus_is_proof': False}
            if key in ('codex', 'grok', 'gemini'):
                from .cli_review import CLIConsultation
                result['isolation'] = CLIConsultation(get_provider(key), (models or {}).get(key),
                                                      cwd=cwd, mode=mode).provenance
            elif key == 'anthropic':
                from .backends.anthropic import claude_code_provenance
                result['isolation'] = claude_code_provenance(cwd or str(config.ROOT), mode)
            if legal:
                result['legal_review'] = assess_legal(answer, inspected)
            return result
    return await asyncio.gather(*(one(key) for key in advisors))
