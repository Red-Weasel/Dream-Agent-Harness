"""The same observed computer-action contract for SDK, CLI bridge and HTTP models."""
from __future__ import annotations

import base64
import json
from pathlib import Path

from claude_agent_sdk import tool

from .. import config
from ..computer import Computer
from .context import ctx, ok, err


def controller():
    context = ctx()
    current = context.computer
    if current is None:
        # Context-owned, not a process singleton: another Engine cannot use these IDs.
        current = context.computer = Computer(context.workspace, config.SCREENSHOT_DIR / 'computer' / context.session_id)
    return current


def result(value):
    output = ok(json.dumps(value, ensure_ascii=False))
    path = value.get('screenshot')
    if path and ctx().multimodal:
        output['content'].append({'type': 'image', 'mimeType': 'image/png',
                                  'data': base64.b64encode(Path(path).read_bytes()).decode('ascii')})
    elif path:
        value = {**value, 'image_note': 'Pixels are not attached: image input unavailable. DOM state remains readable; appearance is unverified.'}
        output = ok(json.dumps(value, ensure_ascii=False))
    return output


@tool('computer_open',
      'Open an owned browser target (explicit http/https URL; separate from Studio/native browser and existing sign-ins), '
      'or attach an explicit X11 desktop window ID. Does not focus a desktop window. Returns current state and a single-use observation_id. '
      'Call computer_observe without a target to check availability/list windows. Requires ordinary tool approval.',
      {'type': 'object', 'properties': {'kind': {'type': 'string', 'enum': ['browser', 'desktop']},
       'url': {'type': 'string'}, 'window_id': {'type': 'string'}}, 'required': ['kind'], 'additionalProperties': False})
async def computer_open(args):
    try:
        return result(await controller().open(args['kind'], url=args.get('url', ''), window_id=args.get('window_id', '')))
    except Exception as exc:
        return err(f'Computer target did not open: {type(exc).__name__}: {exc}')


@tool('computer_observe',
      'Read fresh state and pixels from an owned computer target; returns observation_id and browser element IDs or desktop window-relative dimensions. '
      'Without target_id, reports capabilities/owned targets; list_windows=true explicitly lists X11 windows. '
      'Readiness reports sampled_quiet, unsettled, or pixels_unavailable; animated pages still return fresh evidence. Later changes remain possible. '
      'Use element_offset=next_element_offset for more controls (each page issues a fresh observation ID). '
      'Pixels are attached only when image input is available. Does not click/type/focus or inspect another session.',
      {'type': 'object', 'properties': {'target_id': {'type': 'string'}, 'list_windows': {'type': 'boolean'},
       'element_offset': {'type':'integer','minimum':0}}, 'additionalProperties': False})
async def computer_observe(args):
    try:
        c = controller()
        if args.get('target_id'):
            return result(await c.observe(args['target_id'],offset=args.get('element_offset',0)))
        return result(await c.windows() if args.get('list_windows') else c.capabilities())
    except Exception as exc:
        return err(f'Computer observation failed: {type(exc).__name__}: {exc}')


@tool('computer_action',
      'Perform ONE action on an owned target using its most recent observation_id (consumed even on uncertain dispatch). '
      'Browser type requires element_id; click uses either element_id or viewport CSS x/y. type replaces its field. Desktop click uses unscaled window-relative x/y; '
      'drag takes exactly two points; stroke takes 2–128 ordered {x,y} points with left button held, all inside the observed viewport/window. No scripts. '
      'Optional duration_ms=1–2000 paces drag/stroke with interpolated movement or stationary hold; at most 256 internal points. Omit for existing untimed dispatch. '
      'desktop type inserts at current focus. key uses named keys/chords; browser scroll uses dx/dy, desktop uses only dy and approximates wheel steps. '
      'Browser chords validate before dispatch; Ctrl/CTRL, Cmd/Command, Option, Esc, Return and Del are accepted aliases. '
      'focus is explicit desktop activation. Known state changes refuse replay. Returns fresh state when available; if after-action observation fails, returns dispatched input with missing evidence and an unverified task outcome. '
      'Only pass parameters used by that target/action; others are refused. Desktop pixels are a screen crop and may include overlays. '
      'Errors can follow successful actions: observe before retrying. Actions use normal permissions, including Auto.',
      {'type': 'object', 'properties': {'target_id': {'type': 'string'}, 'observation_id': {'type': 'string'},
       'action': {'type': 'string', 'enum': ['click', 'drag', 'stroke', 'type', 'key', 'scroll', 'focus']},
       'element_id': {'type': 'string'}, 'x': {'type': 'integer'}, 'y': {'type': 'integer'},
       'points': {'type': 'array', 'minItems': 2, 'maxItems': 128, 'items': {'type': 'object',
                  'properties': {'x': {'type': 'integer'}, 'y': {'type': 'integer'}},
                  'required': ['x','y'], 'additionalProperties': False}},
       'duration_ms': {'type': 'integer', 'minimum': 1, 'maximum': 2000},
       'text': {'type': 'string', 'maxLength': 4000}, 'key': {'type': 'string', 'maxLength': 80},
       'dx': {'type': 'integer', 'minimum': -2000, 'maximum': 2000}, 'dy': {'type': 'integer', 'minimum': -2000, 'maximum': 2000}},
       'required': ['target_id', 'observation_id', 'action'], 'additionalProperties': False})
async def computer_action(args):
    try:
        rest = {k: v for k, v in args.items() if k not in {'target_id', 'observation_id', 'action'}}
        return result(await controller().act(args['target_id'], args['observation_id'], args['action'], **rest))
    except Exception as exc:
        return err(f'Computer action failed: {type(exc).__name__}: {exc}')


@tool('computer_close', 'Close a session-owned browser target or detach desktop control. Never closes the desktop application.',
      {'type': 'object', 'properties': {'target_id': {'type': 'string'}}, 'required': ['target_id'], 'additionalProperties': False})
async def computer_close(args):
    try:
        return result(await controller().close(args['target_id']))
    except Exception as exc:
        return err(f'Computer target close failed: {type(exc).__name__}: {exc}')


COMPUTER_TOOLS = [computer_open, computer_observe, computer_action, computer_close]
