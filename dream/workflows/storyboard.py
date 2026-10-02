"""The Frontier review loop's storyboard contract (DREAM-217, ADR-070), schema v1. The writer's reply carries exactly
one storyboard JSON block; validate() checks it before any judging and answers in at most 20 readable lines for the
writer; plan() turns a valid storyboard into frame ranges and keyframes for Dream's fixed Blender script. The
storyboard is data only: nothing here runs it.

Schema v1 (24 fps, 1920x1080, exactly 720 frames = 30 s):
  {"version": 1, "background": "#rrggbb", "shots": [2 to 12 shots]}
  a shot:    {"frames": 12..360, "camera": {"from": P, "to": P, "target": P}, "fade_in": F, "fade_out": F,
              "elements": [0 to 8 elements]}, the shots' frames adding up to exactly 720, fade_in + fade_out at most
              frames - 1, and neither from nor to the same point as target (the camera needs a direction)
  an element: {"type": sphere|ring|disc|plane|text|figure, "at": P, "size": above 0 and at most 50,
              "color": "#rrggbb"}, and for a text element "text": 1 to 60 printable characters
  P is three finite numbers from -100 to 100; F a whole number of frames, 0 or more. No other key is allowed."""
from __future__ import annotations

import json
import math
import re

from ..core.profiles import shown

FPS, WIDTH, HEIGHT, FRAMES = 24, 1920, 1080, 720
TYPES = ('sphere', 'ring', 'disc', 'plane', 'text', 'figure')
MAX_LINES = 20
REPLY_CHARS = 64_000        # a writer's reply longer than this is refused before it is read (a draft is far shorter)
# A model's json code fence, as this module and the judges' parser (frontier.py) read it: the tag in any case, the
# fence lines indented or not, LF or CRLF line endings; each is matched against one line (see fences()).
_OPEN = re.compile(r'[ \t]*```json[ \t]*\r?', re.I)
_CLOSE = re.compile(r'[ \t]*```[ \t]*\r?')
_COLOUR = re.compile(r'#[0-9a-fA-F]{6}\Z')
_TOP = ('version', 'background', 'shots')
_SHOT = ('frames', 'camera', 'fade_in', 'fade_out', 'elements')
_CAMERA = ('from', 'to', 'target')
_ELEMENT = ('type', 'at', 'size', 'color')


class StoryboardError(ValueError):
    """A reply without exactly one storyboard block, a block that is not JSON, or plan() of an invalid storyboard."""


def fences(text: str) -> tuple[list[str], str]:
    """The text's json code fences, found in one pass over its lines (linear time, whatever the text): the bodies
    of the complete ones, left to right, and the text with each of them left out (its lines become one empty line).
    A fence opens with ```json (any case) alone on a line, indented or not, and closes with ``` alone on a later
    line; every line between is its body, another opening included. An unclosed fence stays text."""
    lines = text.split('\n')
    bodies, kept, body, opened = [], [], None, 0
    for n, line in enumerate(lines):
        if body is None:
            if _OPEN.fullmatch(line):
                body, opened = [], n
            else:
                kept.append(line)
        elif _CLOSE.fullmatch(line):
            bodies.append(''.join(part + '\n' for part in body))
            kept.append('')
            body = None
        else:
            body.append(line)
    if body is not None:
        kept.extend(lines[opened:])
    return bodies, '\n'.join(kept)


def extract(reply: object) -> object:
    """The parsed JSON of the reply's one json code block (validate() checks what it is). A reply over
    REPLY_CHARS is refused before it is read."""
    if isinstance(reply, str) and len(reply) > REPLY_CHARS:
        raise StoryboardError(f'The reply is too long: {len(reply):,} characters, over {REPLY_CHARS:,}')
    blocks = fences(reply)[0] if isinstance(reply, str) else []
    if len(blocks) != 1:
        raise StoryboardError('The reply must hold exactly one storyboard JSON block')
    try:
        return json.loads(blocks[0])
    except (json.JSONDecodeError, RecursionError) as exc:
        raise StoryboardError(f'The storyboard block is not valid JSON: {exc}') from None
    except ValueError:              # Python's integer-length limit; its own text is advice for a developer
        raise StoryboardError('The storyboard block is not valid JSON: a number in it is too long') from None


def shown_key(name: object) -> str:
    """A key from a file as a refusal names it: escaped and cut like profiles.shown, and a blank key (empty or only
    whitespace) quoted as JSON writes it, so it can be seen."""
    return shown(name) if str(name).strip() else shown(json.dumps(name))


def validate(data: object) -> list[str]:
    """Every problem as one readable line, "<where>: <what>", in the storyboard's order; at most 20 lines, the last
    then counting the rest. [] when the storyboard is valid."""
    problems: list[str] = []
    _storyboard(data, problems)
    if len(problems) > MAX_LINES:
        problems = problems[:MAX_LINES - 1] + [f'... and {len(problems) - MAX_LINES + 1} more problems']
    return problems


def plan(data: object) -> dict:
    """A valid storyboard's frame ranges and keyframes: shots numbered from 1 with contiguous 1-based [start, end]
    ranges ending at 720; per shot two camera keyframes (`from` at its first frame, `to` at its last, both looking at
    `target`) and its fade keyframes (opacity 0 or 1: up from its first frame over fade_in frames, down to its last
    over fade_out frames, 1 throughout without fades). Numbers become floats and colours lower case, so the same
    storyboard always gives the same JSON. StoryboardError, with validate()'s lines, when it is invalid."""
    problems = validate(data)
    if problems:
        raise StoryboardError('\n'.join(problems))
    shots, start = [], 1
    for number, shot in enumerate(data['shots'], 1):
        end = start + shot['frames'] - 1
        camera = shot['camera']
        fade = {start: 0.0 if shot['fade_in'] else 1.0}
        if shot['fade_in']:
            fade[start + shot['fade_in']] = 1.0
        if shot['fade_out']:
            fade[end - shot['fade_out']] = 1.0
            fade[end] = 0.0
        shots.append({
            'shot': number, 'start': start, 'end': end,
            'camera': [{'frame': start, 'location': _floats(camera['from']), 'target': _floats(camera['target'])},
                       {'frame': end, 'location': _floats(camera['to']), 'target': _floats(camera['target'])}],
            'fade': [{'frame': frame, 'value': value} for frame, value in sorted(fade.items())],
            'elements': [_planned(item) for item in shot['elements']],
        })
        start = end + 1
    return {'version': 1, 'fps': FPS, 'width': WIDTH, 'height': HEIGHT, 'frames': FRAMES,
            'background': data['background'].lower(), 'shots': shots}


def _planned(item: dict) -> dict:
    out = {'type': item['type'], 'at': _floats(item['at']), 'size': float(item['size']),
           'color': item['color'].lower()}
    if item['type'] == 'text':
        out['text'] = item['text']
    return out


def _floats(values: list) -> list[float]:
    return [float(value) + 0.0 for value in values]         # + 0.0 turns -0.0 into 0.0


def _storyboard(data: object, problems: list[str]) -> None:
    if not _keys(data, '', _TOP, problems):
        return
    if 'version' in data and (type(data['version']) is not int or data['version'] != 1):
        problems.append('version: must be 1')
    if 'background' in data:
        _colour(data['background'], 'background', problems)
    if 'shots' not in data:
        return
    shots = data['shots']
    if not isinstance(shots, list) or not 2 <= len(shots) <= 12:
        problems.append('shots: must be a list of 2 to 12 shots')
        if not isinstance(shots, list):
            return
    frames = [_shot(shot, f'shots[{n}]', problems) for n, shot in enumerate(shots)]
    if None not in frames and sum(frames) != FRAMES:
        problems.append(f"shots: the shots' frames add up to {sum(frames)}; they must add up to exactly 720")


def _shot(shot: object, path: str, problems: list[str]) -> int | None:
    """Checks one shot; its frames when they are valid, else None."""
    if not _keys(shot, path, _SHOT, problems):
        return None
    frames = shot.get('frames')
    if 'frames' in shot and not (_whole(frames) and 12 <= frames <= 360):
        problems.append(f'{path}.frames: must be a whole number from 12 to 360')
        frames = None
    if 'camera' in shot and _keys(shot['camera'], f'{path}.camera', _CAMERA, problems):
        camera = shot['camera']
        valid = [key for key in _CAMERA if key in camera and _point(camera[key], f'{path}.camera.{key}', problems)]
        if 'target' in valid:
            for key in ('from', 'to'):
                if key in valid and camera[key] == camera['target']:
                    problems.append(f'{path}.camera.{key}: must not be the same point as target, or the camera has '
                                    'no direction to look in')
    fades = []
    for key in ('fade_in', 'fade_out'):
        if key in shot:
            if _whole(shot[key]) and shot[key] >= 0:
                fades.append(shot[key])
            else:
                problems.append(f'{path}.{key}: must be a whole number, 0 or more')
    if frames is not None and len(fades) == 2 and sum(fades) > frames - 1:
        problems.append(f'{path}: fade_in + fade_out must be at most frames - 1 ({frames - 1})')
    if 'elements' in shot:
        elements = shot['elements']
        if not isinstance(elements, list) or len(elements) > 8:
            problems.append(f'{path}.elements: must be a list of at most 8 elements')
        if isinstance(elements, list):
            for n, item in enumerate(elements):
                _element(item, f'{path}.elements[{n}]', problems)
    return frames


def _element(item: object, path: str, problems: list[str]) -> None:
    if not _keys(item, path, _ELEMENT, problems, optional=('text',)):
        return
    kind = item.get('type')
    if 'type' in item and kind not in TYPES:
        problems.append(f'{path}.type: must be one of sphere, ring, disc, plane, text, figure')
    if 'at' in item:
        _point(item['at'], f'{path}.at', problems)
    if 'size' in item and not (_number(item['size']) and 0 < item['size'] <= 50):
        problems.append(f'{path}.size: must be a finite number above 0 and at most 50')
    if 'color' in item:
        _colour(item['color'], f'{path}.color', problems)
    if kind == 'text':
        text = item.get('text')
        if 'text' not in item:
            problems.append(f'{path}.text: missing')
        elif not (isinstance(text, str) and 1 <= len(text) <= 60 and text.isprintable()):
            problems.append(f'{path}.text: must be 1 to 60 printable characters')
    elif 'text' in item and kind in TYPES:
        problems.append(f'{path}.text: only a text element has text')


def _keys(value: object, path: str, keys: tuple[str, ...], problems: list[str], optional: tuple[str, ...] = ()) -> bool:
    """Whether the value is an object; its unknown and missing keys are problems."""
    if not isinstance(value, dict):
        problems.append(f'{path or "storyboard"}: must be a JSON object')
        return False
    prefix = path + '.' if path else ''
    for key in value:
        if key not in keys and key not in optional:
            problems.append(f'{prefix}{shown_key(key)}: unknown key')
    for key in keys:
        if key not in value:
            problems.append(f'{prefix}{key}: missing')
    return True


def _whole(value: object) -> bool:
    return type(value) is int


def _number(value: object) -> bool:
    return type(value) is int or (type(value) is float and math.isfinite(value))


def _point(value: object, path: str, problems: list[str]) -> bool:
    if isinstance(value, list) and len(value) == 3 and all(_number(v) and -100 <= v <= 100 for v in value):
        return True
    problems.append(f'{path}: must be three finite numbers from -100 to 100')
    return False


def _colour(value: object, path: str, problems: list[str]) -> None:
    if not (isinstance(value, str) and _COLOUR.match(value)):
        problems.append(f'{path}: must be a hex colour like #1a2b3c')
