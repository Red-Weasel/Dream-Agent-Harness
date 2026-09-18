"""Validated, editable scene descriptions and a portable deterministic player."""
from __future__ import annotations

import copy
import json
import math
import re
from pathlib import Path
from urllib.parse import urlsplit


def default_composition() -> dict:
    return validate_composition({"width": 1280, "height": 720, "fps": 24, "scenes": [
        {"id": "intro", "duration": 5, "title": "Your software. Clearly explained.",
         "body": "Turn the work you do into a story people can follow.", "animation": "slide"},
        {"id": "workflow", "duration": 5, "title": "Show the workflow",
         "body": "Add a screenshot or a video, then describe what matters.", "animation": "zoom"},
        {"id": "close", "duration": 5, "title": "Make the next step obvious",
         "body": "Create. Review. Refine.", "animation": "fade"},
    ]})


def _number(value, name, lower, upper, *, integer=False):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{name} must be a number")
    if not math.isfinite(value) or not lower <= value <= upper:
        raise ValueError(f"{name} must be between {lower} and {upper}")
    if integer and int(value) != value:
        raise ValueError(f"{name} must be an integer")
    return int(value) if integer else float(value)


def validate_composition(value: dict) -> dict:
    if not isinstance(value, dict):
        raise ValueError('Composition must be an object')
    result = copy.deepcopy(value)
    for name, default, lo, hi in [('width', 1920, 160, 3840), ('height', 1080, 90, 2160),
                                   ('fps', 30, 1, 60)]:
        result[name] = _number(value.get(name, default), name, lo, hi, integer=True)
    if result['width'] % 2 or result['height'] % 2:
        raise ValueError('Video dimensions must be even')
    scenes = result.get('scenes')
    if not isinstance(scenes, list) or not 1 <= len(scenes) <= 500:
        raise ValueError('Composition needs 1 to 500 scenes')
    ids = set()
    for index, scene in enumerate(scenes):
        if not isinstance(scene, dict):
            raise ValueError('Each scene must be an object')
        scene.setdefault('id', f'scene-{index + 1}')
        if not isinstance(scene['id'], str) or not re.fullmatch(r'[\w-]{1,80}', scene['id']):
            raise ValueError('Scene id must contain letters, digits, underscores or hyphens')
        if scene['id'] in ids:
            raise ValueError('Scene ids must be unique')
        ids.add(scene['id'])
        scene['duration'] = _number(scene.get('duration', 5), 'duration', .1, 600)
        for name, default in [('title', ''), ('body', ''), ('label', '')]:
            scene.setdefault(name, default)
            if not isinstance(scene[name], str) or len(scene[name]) > 4000:
                raise ValueError(f'{name} must be text of at most 4000 characters')
        for name, default in [('background', '#09252d'), ('accent', '#92e3cf')]:
            scene.setdefault(name, default)
            if not isinstance(scene[name], str) or not re.fullmatch(r'#[0-9a-fA-F]{6}', scene[name]):
                raise ValueError(f'{name} must be a six-digit hex color')
        scene.setdefault('animation', 'fade')
        if scene['animation'] not in ('fade', 'slide', 'zoom', 'none'):
            raise ValueError('Animation must be fade, slide, zoom or none')
        scene.setdefault('asset_id', '')
        if not isinstance(scene['asset_id'], str) or len(scene['asset_id']) > 128:
            raise ValueError('Invalid asset id')
        scene.setdefault('asset_type', 'image')
        if scene['asset_type'] not in ('image', 'video'):
            raise ValueError('Asset type must be image or video')
    duration = sum(s['duration'] for s in scenes)
    if duration > 3600:
        raise ValueError('A composition can be at most one hour')
    if not isinstance(result.get('audio_asset_id', ''), str):
        raise ValueError('Invalid audio asset id')
    result['version'] = 1
    return result


def build_html(composition: dict, assets: dict[str, str]) -> str:
    composition = validate_composition(composition)
    for url in assets.values():
        if not isinstance(url, str) or urlsplit(url).scheme not in ('file', 'data'):
            raise ValueError('Media player accepts only explicit local or embedded assets')
        if url.startswith('data:') and not re.match(r'data:(image|video|audio)/[\w.+-]+;base64,', url):
            raise ValueError('Embedded assets must be base64 image, video or audio')
    required = [s['asset_id'] for s in composition['scenes'] if s['asset_id']]
    if composition.get('audio_asset_id'):
        required.append(composition['audio_asset_id'])
    missing = [key for key in required if key not in assets]
    if missing:
        raise ValueError(f'Missing project assets: {", ".join(missing)}')
    payload = json.dumps({'composition': composition, 'assets': assets}, allow_nan=False)
    payload = payload.replace('<', '\\u003c').replace('>', '\\u003e').replace('&', '\\u0026')
    template = Path(__file__).with_name('player.html').read_text(encoding='utf-8')
    return template.replace('/*DREAM_DATA*/{}', payload)
