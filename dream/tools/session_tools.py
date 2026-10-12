"""Node-scoped DeepSleep tools; the parent bridge enforces link authority."""
from __future__ import annotations

import json
import os
import uuid
from urllib.parse import urlsplit

import httpx
from claude_agent_sdk import tool

from ..core.linked_sessions import IDENTIFIER, MAX_PROMPT, current_envelope
from .context import ok, err


def enabled() -> bool:
    return bool(os.environ.get('DREAM_DEEPSLEEP_NODE_ID'))


def _configuration() -> tuple[str, str, str]:
    node = os.environ.get('DREAM_DEEPSLEEP_NODE_ID', '')
    url = os.environ.get('DREAM_DEEPSLEEP_BRIDGE_URL', '')
    token = os.environ.get('DREAM_DEEPSLEEP_BRIDGE_TOKEN', '')
    parsed = urlsplit(url)
    try:
        valid_port = parsed.port is not None and 0 < parsed.port < 65536
    except ValueError:
        valid_port = False
    if (not IDENTIFIER.fullmatch(node) or not 16 <= len(token) <= 256
            or any(ord(ch) < 33 or ord(ch) > 126 for ch in token)
            or parsed.scheme != 'http' or parsed.hostname not in {'127.0.0.1', '::1'}
            or not valid_port or parsed.username or parsed.password or parsed.query or parsed.fragment
            or parsed.path != '/api/deepsleep/bridge'):
        raise ValueError('DeepSleep session bridge is not configured.')
    return node, url, token


async def _request(operation: str, fields: dict) -> dict:
    try:
        _, url, token = _configuration()
        async with httpx.AsyncClient(timeout=10, trust_env=False, follow_redirects=False) as client:
            async with client.stream('POST', url, headers={'X-Dream-Token': token},
                                     json={'operation': operation, **fields}) as response:
                if response.status_code in (401, 403):
                    return err('This session bridge is no longer authorized. Restart this terminal from DeepSleep to reconnect.')
                if response.status_code not in (200, 400):
                    return err('The linked-session bridge refused the request. Check the connection and session state.')
                body = bytearray()
                async for chunk in response.aiter_bytes():
                    body.extend(chunk)
                    if len(body) > 262144:
                        return err('The linked-session response exceeded its size limit.')
        data = json.loads(body)
        if response.status_code == 400:
            messages = {
                'link_required': 'Connect a directed link from this terminal to the receiver before sending.',
                'unknown_target': 'That terminal is not on this board. Use an exact node ID from session_list.',
                'target_not_ready': 'The receiving terminal is not ready. Check its state in DeepSleep.',
                'id_conflict': 'This message ID belongs to different content. Inspect session_inbox before sending a new message.',
                'route_blocked': 'The message would revisit a session or exceed the hop limit; delivery was blocked.',
                'route_unknown': 'The incoming task for this route could not be verified; delivery was blocked.',
                'receipt_unknown': 'No receipt with that ID is visible to this session.',
                'capacity_reached': 'This workspace reached its message or handoff limit. Open a fresh workspace session.',
                'link_limit': 'This link carried 20 messages in the last hour, the limit. Post to the board (ram_post) instead, or wait.',
            }
            code = data.get('code') if isinstance(data, dict) else None
            return err(messages.get(code, 'The linked-session request is invalid. Check the target, message and session state.')
                       if isinstance(code, str) else 'The linked-session bridge returned an invalid error response.')
        if not isinstance(data, dict) or data.get('ok') is not True or 'result' not in data:
            return err('The linked-session bridge returned an invalid response.')
        rendered = json.dumps(data['result'], ensure_ascii=False)
        # The bridge owns response schemas. Still never relay its credential if
        # a broken endpoint includes it in an error or result.
        rendered = rendered.replace(token, '[redacted]')
        return ok(rendered)
    except (OSError, httpx.HTTPError, ValueError, RecursionError):
        return err('The linked-session request could not be confirmed. Inspect the inbox before retrying with the same message ID.')


@tool('session_list', 'Who you are on this DeepSleep board and which terminals you are directly linked to: sends_to (you can message them) and hears_from (they can message you). Direct links only; the project message board is ram_read/ram_post and needs no link.',
      {'type': 'object', 'properties': {}, 'additionalProperties': False})
async def session_list(args: dict) -> dict:
    if args:
        return err('session_list accepts no arguments.')
    return await _request('list', {})


@tool('session_send', 'Send to an exact node ID from session_list. Direct, not through the message board. Dream workflow receivers queue tasks; native CLI receivers find it in their link inbox. A link carries at most 20 messages an hour. Returns a receipt; inspect session_inbox and reuse the message ID on retry.',
      {'type': 'object', 'properties': {'target': {'type': 'string'}, 'prompt': {'type': 'string', 'maxLength': MAX_PROMPT},
                                       'id': {'type': 'string', 'description': 'Stable message ID for safe retries; generated when omitted.'}},
       'required': ['target', 'prompt'], 'additionalProperties': False})
async def session_send(args: dict) -> dict:
    if not isinstance(args, dict) or set(args) - {'target', 'prompt', 'id'}:
        return err('session_send needs target, prompt and an optional message ID.')
    target, prompt, identifier = args.get('target'), args.get('prompt'), args.get('id', uuid.uuid4().hex)
    if (not isinstance(target, str) or not IDENTIFIER.fullmatch(target)
            or not isinstance(identifier, str) or not IDENTIFIER.fullmatch(identifier)
            or not isinstance(prompt, str) or not prompt.strip() or '\x00' in prompt
            or len(prompt) > MAX_PROMPT or len(prompt.encode('utf-8', errors='replace')) > 65536):
        return err('The linked-session target, message ID or prompt is invalid.')
    fields = {'target': target, 'prompt': prompt, 'id': identifier}
    envelope = current_envelope()
    if envelope is not None:
        fields.update(message_id=envelope['id'], root_id=envelope['root_id'], visited=[*envelope['visited'], envelope['target']],
                      hops=envelope['hops'] + 1)
    result = await _request('send', fields)
    if result.get('is_error'):
        result['content'][0]['text'] += ' Message ID: ' + identifier
    return result


@tool('session_inbox', 'Read recent task receipts and manual handoffs. Prepared means awaiting owner review, not read by an agent. Supply a receipt id for its full completed answer or framed handoff.',
      {'type': 'object', 'properties': {'id': {'type': 'string'}}, 'additionalProperties': False})
async def session_inbox(args: dict) -> dict:
    if not isinstance(args, dict) or set(args) - {'id'}:
        return err('session_inbox accepts only an optional receipt id.')
    if 'id' in args and (not isinstance(args['id'], str) or not IDENTIFIER.fullmatch(args['id'])):
        return err('Invalid receipt identifier.')
    return await _request('inbox', args)


SESSION_TOOLS = [session_list, session_send, session_inbox]
