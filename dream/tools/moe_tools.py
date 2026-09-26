"""Dream MoE tools: consult one advisor, or convene the whole council.

Registered in every Dream session. Both tools read the advisor line-up from
``ctx().moe_config`` and delegate to ``dream.core.moe``; in an ordinary session (no
council configured) they return a clean error rather than doing anything.
"""

from __future__ import annotations

import json
from typing import Any

from claude_agent_sdk import SdkMcpTool, tool

from ..core.providers import get_provider
from .context import ctx, err, ok

_LEGAL_PROPERTIES = {
    'legal': {'type': 'boolean', 'description': 'Require attributed legal claims, dissent, and inspectable source quotations. Agreement is not proof.'},
    'sources': {'type': 'array', 'maxItems': 12, 'description': 'Local source artifacts within the worker workspace; URLs alone are not inspected evidence.',
                'items': {'type': 'object', 'properties': {'id': {'type': 'string'}, 'path': {'type': 'string'}, 'url': {'type': 'string'}}, 'required': ['id', 'path']}},
}


def _options(cfg, args):
    options = {}
    for source, target in (('max_concurrency', 'max_concurrency'), ('timeout_seconds', 'timeout'), ('advisor_models', 'models'), ('advisor_efforts', 'efforts')):
        value = getattr(cfg, source, None)
        if value is not None:
            options[target] = value
    if args.get('legal') or getattr(cfg, 'legal_review', False):
        options.update(legal=True, sources=args.get('sources', []))
    return options


def _format_results(results):
    blocks = []
    for result in results:
        block = f"### {result['label']}\n{result['answer']}"
        if 'isolation' in result:
            block += '\n\nRun provenance: ' + json.dumps(result['isolation'], ensure_ascii=False)
        if 'legal_review' in result:
            block += '\n\nEvidence check (agreement is not proof; legal correctness unverified):\n'
            block += json.dumps(result['legal_review'], ensure_ascii=False, indent=2)
        blocks.append(block)
    return '\n\n'.join(blocks)

_CONSULT_SCHEMA = {
    "type": "object",
    "properties": {
        **_LEGAL_PROPERTIES,
        "advisor": {
            "type": "string",
            "description": "Provider key of the advisor to ask — must be one of the "
            "council's advisors (e.g. 'grok', 'codex', 'gemini').",
        },
        "question": {"type": "string", "description": "The question to put to the advisor."},
        "context": {
            "type": "string",
            "description": "Optional background to prepend so the advisor has what it needs.",
        },
    },
    "required": ["advisor", "question"],
}


@tool(
    "consult",
    "Ask ONE named advisor model for its independent take on a question. Use this when "
    "you want a specific council member's second opinion before a hard call; attribute "
    "their input when you use it. Agreement is not proof. For legal questions set legal=true and supply source artifacts.",
    _CONSULT_SCHEMA,
)
async def consult(args: dict[str, Any]) -> dict[str, Any]:
    c = ctx()
    cfg = c.moe_config
    if cfg is None:
        return err("Configure Council (Dream MoE) advisors before using consult.")
    advisor = args["advisor"]
    if advisor not in cfg.advisors:
        return err(
            f"'{advisor}' is not on the council. Advisors: {', '.join(cfg.advisors)}."
        )
    from ..core import moe  # lazy: advisors live in core, avoid an import-time cycle

    options = _options(cfg, args)
    mode_getter = getattr(c, 'mode_getter', None)
    if mode_getter is not None:
        options['mode'] = mode_getter()
    try:
        if options.get('legal'):
            results = await moe.council([advisor], args['question'], args.get('context', ''), cwd=str(c.workspace), **options)
            return ok(_format_results(results))
        kwargs = {'cwd': str(c.workspace)}
        if 'mode' in options:
            kwargs['mode'] = options['mode']
        if 'timeout' in options:
            kwargs['timeout'] = options['timeout']
        if advisor in options.get('models', {}):
            kwargs['model'] = options['models'][advisor]
        if advisor in options.get('efforts', {}):
            kwargs['effort'] = options['efforts'][advisor]
        answer = await moe.consult_advisor(advisor, args['question'], args.get('context', ''), **kwargs)
    except (ValueError, OSError, TypeError, KeyError) as exc:
        return err(f'Advisor configuration/evidence unavailable: {exc}')
    try:
        label = get_provider(advisor).label
    except ValueError:
        label = advisor
    provenance = ''
    if advisor in ('codex', 'grok', 'gemini'):
        provenance = ("CLI consultation runs like the main CLI path: the owner's CLI configuration, "
                      "this workspace, its own tools, sandboxed by Dream's permission mode.\n")
    elif advisor == 'anthropic':
        provenance = ("Claude consultation runs like the main Claude path: this workspace, Dream's tools, "
                      "Dream's policy in the permission mode; anything that would need your approval is refused.\n")
    return ok(f"{label} says:\n{provenance}{answer}")


_COUNCIL_SCHEMA = {
    "type": "object",
    "properties": {
        **_LEGAL_PROPERTIES,
        "question": {"type": "string", "description": "The question to put to the whole council."},
        "context": {
            "type": "string",
            "description": "Optional shared background prepended for every advisor.",
        },
    },
    "required": ["question"],
}


@tool(
    "council",
    "Ask ALL advisors in parallel and get their takes side by side. Use this before a "
    "large, contested, or irreversible decision to gather the full council's views, then "
    "decide yourself and attribute what you drew on. Preserve dissent; consensus is never proof. For legal questions set legal=true and supply source artifacts.",
    _COUNCIL_SCHEMA,
)
async def council(args: dict[str, Any]) -> dict[str, Any]:
    c = ctx()
    cfg = c.moe_config
    if cfg is None:
        return err("Configure Council (Dream MoE) advisors before using council.")
    if not cfg.advisors:
        return err("No advisors are configured. Add an advisor in Council controls.")
    from ..core import moe  # lazy: advisors live in core, avoid an import-time cycle

    try:
        options = _options(cfg, args)
        mode_getter = getattr(c, 'mode_getter', None)
        if mode_getter is not None:
            options['mode'] = mode_getter()
        results = await moe.council(
            cfg.advisors, args["question"], args.get("context", ""), cwd=str(c.workspace), **options
        )
    except (ValueError, OSError, TypeError, KeyError) as exc:
        return err(f'Council configuration/evidence unavailable: {exc}')
    return ok(_format_results(results))


def moe_tools() -> list[SdkMcpTool]:
    """Council tools; enabled advisors are read from the current session."""
    return [consult, council]
