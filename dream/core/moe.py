"""Dream MoE — the council: config + one-shot advisor consultations.

An orchestrator provider runs a normal Dream session and can poll a set of *advisors*
— other frontier engines — for an independent take. Each consultation is a single
headless run on that advisor's provider. A CLI advisor runs like the main-model CLI
path (DREAM-136): the owner's CLI configuration, the Dream workspace as cwd, its own
tools, sandboxed by Dream's permission mode. Its provenance is retained with the answer.
The three per-provider-kind invokers are separate module-level functions so the
council's fan-out and failure-tolerance can be exercised without spawning a CLI or
touching the network (tests monkeypatch the invokers).
"""

from __future__ import annotations

import asyncio
import json
from dataclasses import asdict, dataclass, field
import os

from .. import config
from .evaluator import IsolatedOpenAIBackend as OpenAICompatBackend
from .council_evidence import assess_legal, prepare_sources
from .run_state import atomic_write
from .providers import Provider, get_provider
from .profiles import resolve_profile
from .review_usage import attributed_meter

CONFIG_PATH = config.VAR_DIR / "moe.json"

# Framing every advisor is booted with. CLI advisors run with their own tools
# under Dream's permission mode (DREAM-136); the framing does not promise otherwise.
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


def load_config() -> MoeConfig | None:
    """The saved council, or ``None`` if there is none / the file is unreadable."""
    try:
        data = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
        from .council_config import parse_config
        return parse_config(data)
    except (FileNotFoundError, OSError, ValueError, KeyError, TypeError):
        return None


def save_config(cfg: MoeConfig) -> None:
    """Persist ``{orchestrator, advisors}`` (creating the var dir if needed)."""
    CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    atomic_write(CONFIG_PATH, json.dumps(asdict(cfg), indent=2) + '\n')


# --- one-shot advisor calls, one per provider kind ---------------------------


def _context_meter(provider):
    return attributed_meter(provider, scope='council')


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
                       mode: str | None = None) -> str:
    from .cli_review import CLIConsultation
    consultation = CLIConsultation(provider, model, runtime_meter=_context_meter(provider), cwd=cwd, mode=mode)
    try:
        consultation.prepare()
        if effort is not None:
            from .council_config import validate_effort
            validate_effort(provider.key, effort, model)
            # Codex exec accepts per-invocation configuration before stdin '-'.
            consultation.argv[-1:-1] = ['-c', 'model_reasoning_effort=' + json.dumps(effort)]
        return await consultation.run(ADVISOR_SYSTEM + '\n\n' + prompt)
    finally:
        await consultation._stop()
        consultation.close()


async def _consult_anthropic(provider: Provider, prompt: str, *, cwd: str | None = None, model=None, effort=None) -> str:
    """One-shot Claude Agent SDK query — no tools, no acting."""
    import types
    from claude_agent_sdk import (
        AssistantMessage,
        ClaudeAgentOptions,
        ResultMessage,
        TextBlock,
        PermissionResultDeny,
        query,
    )

    async def deny_tools(*args):
        return PermissionResultDeny(message='Advisors have no tool access')

    opts = ClaudeAgentOptions(
        system_prompt=ADVISOR_SYSTEM,
        tools=[], allowed_tools=[], mcp_servers={}, strict_mcp_config=True, setting_sources=[],
        permission_mode="default", can_use_tool=deny_tools,
        settings='{"disableAllHooks":true}',
        cwd=cwd or str(config.ROOT), max_turns=1,
        model=model or provider.default_model,
        effort=effort,
    )
    text = ""
    meter = _context_meter(provider)
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
            if isinstance(msg, AssistantMessage):
                for block in msg.content:
                    if isinstance(block, TextBlock):
                        text += block.text
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
    return text.strip() or '[unavailable — advisor returned no answer]'


async def _consult_openai(provider: Provider, prompt: str, *, cwd: str | None = None, model=None, effort=None) -> str:
    """One-shot call against an OpenAI-compatible provider, no tools."""
    selected_model = model or provider.default_model or 'default'
    backend = OpenAICompatBackend(
        provider=provider,
        model=selected_model,
        profile=resolve_profile(provider, model=selected_model),
        system_prompt=ADVISOR_SYSTEM,
        tools=[],
        permission_cb=None,
    )
    if effort is not None:
        backend.set_effort(effort)
    meter = _context_meter(provider)
    if meter is not None:
        backend.runtime_meter = meter
    return await _run_backend(backend, prompt)


async def consult_advisor(
    provider_key: str, question: str, context: str = "", *, cwd: str | None = None,
    model: str | None = None, timeout: float | None = None, effort: str | None = None,
    mode: str | None = None,
) -> str:
    """Ask one advisor for its take. Dispatches by provider kind. Never raises —
    any failure comes back as a short ``[label: unavailable — reason]`` string.

    The invoker is looked up by bare name (not a captured dict) so tests can
    monkeypatch ``_consult_cli`` / ``_consult_anthropic`` / ``_consult_openai``.
    ``mode`` is Dream's permission mode; only a CLI advisor takes it (its sandbox)."""
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
        if mode is not None and provider.kind == 'cli':
            kwargs['mode'] = mode
        invoker = {'cli': _consult_cli, 'anthropic': _consult_anthropic, 'openai': _consult_openai}.get(provider.kind)
        if invoker is None:
            return f"[{label}: unavailable — unknown provider kind '{provider.kind}']"
        return await asyncio.wait_for(invoker(provider, prompt, **kwargs), timeout=seconds)
    except Exception as e:  # noqa: BLE001 — an advisor must never sink the caller
        return f"[{label}: unavailable — {type(e).__name__}: {e}]"


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
            if legal:
                result['legal_review'] = assess_legal(answer, inspected)
            return result
    return await asyncio.gather(*(one(key) for key in advisors))
