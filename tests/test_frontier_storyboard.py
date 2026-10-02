"""DREAM-217, the Frontier review loop's storyboard contract (F2; ADR-070): extract() takes exactly one storyboard JSON
block from the writer's reply, validate() checks schema v1 and answers in at most 20 readable lines, and plan() turns
a valid storyboard into contiguous 1-based frame ranges ending at 720 with camera and fade keyframes, the same JSON
for the same input. Pure code: no model, no Blender, nothing written."""
from __future__ import annotations

import copy
import importlib
import json
import random
import string

import pytest

ONE_BLOCK = 'The reply must hold exactly one storyboard JSON block'
TYPES = ['sphere', 'ring', 'disc', 'plane', 'text', 'figure']


def sb():
    """The module under test, imported in each test so that each one fails on its own while the module is missing."""
    return importlib.import_module('dream.workflows.storyboard')


def element(kind='sphere', **over):
    item = {'type': kind, 'at': [0, 0, 1], 'size': 1.5, 'color': '#f2b134'}
    if kind == 'text':
        item['text'] = 'Dream v0.3'
    item.update(over)
    return item


def shot(frames, **over):
    item = {'frames': frames, 'camera': {'from': [0, -8, 2], 'to': [0, -6, 2], 'target': [0, 0, 1]},
            'fade_in': 0, 'fade_out': 0, 'elements': [element()]}
    item.update(over)
    return item


def board(*frames):
    frames = frames or (72, 96, 120, 144, 168, 120)
    return {'version': 1, 'background': '#0b0d10', 'shots': [shot(n) for n in frames]}


def errors(data):
    return sb().validate(data)


# --- extract --------------------------------------------------------------------------------------------------------

def test_extract_takes_the_one_json_block_from_the_prose():
    data = board()
    reply = f'Script: the dreamer wakes.\n\n```json\n{json.dumps(data, indent=2)}\n```\nThat is the storyboard.'
    assert sb().extract(reply) == data


@pytest.mark.parametrize('reply', [
    'No storyboard here.',
    '```\n{"version": 1}\n```',                                   # a fence that is not tagged json
    '```jsonc\n{"version": 1}\n```',                              # nor a tag that only starts with json
    '```json5\n{"version": 1}\n```',
    '```python\nprint(1)\n```',
    '```json\n{"version": 1}\n```\nand again\n```json\n{"version": 1}\n```',
    '```json\n{}\n```\n```json\n{}\n```\n```json\n{}\n```',
    None,
])
def test_extract_needs_exactly_one_json_block(reply):
    with pytest.raises(sb().StoryboardError) as caught:
        sb().extract(reply)
    assert str(caught.value) == ONE_BLOCK


def test_a_block_that_is_not_json_is_refused_readably():
    with pytest.raises(sb().StoryboardError) as caught:
        sb().extract('```json\n{"version": 1,}\n```')
    assert str(caught.value).startswith('The storyboard block is not valid JSON: ')
    with pytest.raises(sb().StoryboardError) as caught:
        sb().extract('```json\n' + '[' * 60_000 + '\n```')             # nested past the decoder's limit, under the cap
    assert str(caught.value).startswith('The storyboard block is not valid JSON: ')


def test_a_reply_over_64000_characters_is_refused_at_once():
    import time
    started = time.process_time()                        # CPU time: a starved SCHED_IDLE run cannot fail it
    with pytest.raises(sb().StoryboardError) as caught:
        sb().extract('```json\n' * 25_000)                # 200,000 characters of unclosed fences
    assert time.process_time() - started < 0.1           # the regex this replaced took 18.6 s here
    assert str(caught.value) == 'The reply is too long: 200,000 characters, over 64,000'
    assert sb().REPLY_CHARS == 64_000


def test_a_reply_of_exactly_64000_characters_is_read():
    with pytest.raises(sb().StoryboardError) as caught:
        sb().extract('x' * 64_000)
    assert str(caught.value) == ONE_BLOCK                  # read, and holding no block
    with pytest.raises(sb().StoryboardError) as caught:
        sb().extract('x' * 64_001)
    assert str(caught.value) == 'The reply is too long: 64,001 characters, over 64,000'


def test_unclosed_fences_under_the_cap_are_scanned_in_linear_time():
    import time
    started = time.process_time()                        # CPU time: a starved SCHED_IDLE run cannot fail it
    with pytest.raises(sb().StoryboardError) as caught:
        sb().extract('```json\n' * 7_990)                  # 63,920 characters: under the cap
    assert time.process_time() - started < 0.3           # about 1 ms; the regex this replaced took 1.85 s here
    assert str(caught.value) == ONE_BLOCK


def test_the_line_scan_finds_what_the_old_fence_pattern_found():
    # the regex of the fix round, kept here as the oracle for the linear line scan that replaced it
    import random
    import re
    oracle = re.compile(r'^[ \t]*```json[ \t]*\r?\n(.*?)^[ \t]*```[ \t]*\r?$', re.M | re.S | re.I)
    pieces = ['```json', '```JSON', '  ```json', '\t```Json  ', '```', '  ```', '```jsonc', '``` json', '````',
              '{"a": 1}', 'prose', '', ' ', '```json x', '~~~json', '```\r', '```json\r', 'text ```json', '```  \r']
    rng = random.Random(1)
    for _ in range(3000):
        text = ''.join(rng.choice(pieces) + rng.choice(['\n', '\n', '\r\n', '']) for _ in range(rng.randint(0, 14)))
        bodies, prose = sb().fences(text)
        assert bodies == oracle.findall(text), repr(text)
        assert prose == oracle.sub('', text), repr(text)


def test_a_number_too_long_to_read_is_named_without_pythons_advice():
    with pytest.raises(sb().StoryboardError) as caught:
        sb().extract('```json\n{"version": 1' + '0' * 5000 + '}\n```')
    assert str(caught.value) == 'The storyboard block is not valid JSON: a number in it is too long'


@pytest.mark.parametrize('opening, closing', [
    ('```JSON\n', '\n```'),                           # the tag in any case
    ('```Json\n', '\n```'),
    ('  ```json\n', '\n  ```'),                       # an indented block
    ('\t```json \n', '\n\t```  '),
    ('```json\r\n', '\r\n```\r\n'),                   # CRLF line endings
    ('   ```JSON\r\n', '\r\n   ```\r\n'),
])
def test_a_models_block_variants_are_read_and_still_only_one_is_allowed(opening, closing):
    data = board()
    block = opening + json.dumps(data, indent=2).replace('\n', '\r\n' if '\r' in opening else '\n') + closing
    assert sb().extract('The storyboard:\n' + block + '\nDone.') == data
    with pytest.raises(sb().StoryboardError) as caught:
        sb().extract(block + '\nand again:\n' + block)
    assert str(caught.value) == ONE_BLOCK


# --- validate -------------------------------------------------------------------------------------------------------

def test_a_valid_storyboard_has_no_errors():
    assert errors(board()) == []
    assert errors(board(360, 360)) == []
    assert errors(board(*[60] * 12)) == []


def test_the_frames_must_add_up_to_exactly_720():
    assert errors(board(72, 96, 120, 144, 168, 119)) == [
        "shots: the shots' frames add up to 719; they must add up to exactly 720"]
    assert errors(board(72, 96, 120, 144, 168, 121)) == [
        "shots: the shots' frames add up to 721; they must add up to exactly 720"]
    assert errors(board(360, 359)) == ["shots: the shots' frames add up to 719; they must add up to exactly 720"]


@pytest.mark.parametrize('shots, line', [
    ([shot(720)], 'shots: must be a list of 2 to 12 shots'),
    ([shot(60)] * 13, 'shots: must be a list of 2 to 12 shots'),
    ([], 'shots: must be a list of 2 to 12 shots'),
    ('two shots', 'shots: must be a list of 2 to 12 shots'),
])
def test_two_to_twelve_shots(shots, line):
    data = board()
    data['shots'] = shots
    assert line in errors(data)


@pytest.mark.parametrize('value', [11, 361, 0, -72, 72.0, '72', True, None, float('nan')])
def test_a_shots_frames_are_a_whole_number_from_12_to_360(value):
    data = board()
    data['shots'][2]['frames'] = value
    assert errors(data) == ['shots[2].frames: must be a whole number from 12 to 360']   # no sum line on top


@pytest.mark.parametrize('where, value', [
    (('shots', 0, 'camera', 'from'), [0, float('nan'), 2]),
    (('shots', 0, 'camera', 'to'), [0, float('inf'), 2]),
    (('shots', 0, 'camera', 'target'), [0, 0, float('-inf')]),
    (('shots', 1, 'elements', 0, 'at'), [0, 0, 100.5]),
    (('shots', 1, 'elements', 0, 'at'), [-101, 0, 0]),
    (('shots', 1, 'elements', 0, 'at'), [0, 0]),
    (('shots', 1, 'elements', 0, 'at'), [0, 0, 0, 0]),
    (('shots', 1, 'elements', 0, 'at'), [0, True, 0]),
    (('shots', 1, 'elements', 0, 'at'), [0, '1', 0]),
    (('shots', 1, 'elements', 0, 'at'), 'centre'),
])
def test_positions_are_three_finite_numbers_from_minus_100_to_100(where, value):
    data = board()
    node = data
    for part in where[:-1]:
        node = node[part]
    node[where[-1]] = value
    path = '.'.join(str(p) for p in where).replace('.0.', '[0].').replace('.1.', '[1].')
    assert errors(data) == [f'{path}: must be three finite numbers from -100 to 100']


def test_the_camera_needs_a_direction_to_look_at_both_ends():
    data = board()
    data['shots'][0]['camera'] = {'from': [0, 0, 1], 'to': [0, -6, 2], 'target': [0.0, 0.0, 1.0]}
    data['shots'][1]['camera'] = {'from': [0, -8, 2], 'to': [3, 4, -0.0], 'target': [3, 4, 0]}
    data['shots'][2]['camera'] = {'from': [5, 5, 5], 'to': [5, 5, 5], 'target': [5, 5, 5]}
    assert errors(data) == [
        'shots[0].camera.from: must not be the same point as target, or the camera has no direction to look in',
        'shots[1].camera.to: must not be the same point as target, or the camera has no direction to look in',
        'shots[2].camera.from: must not be the same point as target, or the camera has no direction to look in',
        'shots[2].camera.to: must not be the same point as target, or the camera has no direction to look in']
    data = board()
    data['shots'][0]['camera'] = {'from': [0, -8, 2], 'to': [0, -8, 2], 'target': [0, 0, 1]}     # a still camera
    assert errors(data) == []


@pytest.mark.parametrize('value', [0, -1, 50.01, float('nan'), float('inf'), '1', True, None])
def test_a_size_is_a_finite_number_above_0_and_at_most_50(value):
    data = board()
    data['shots'][0]['elements'][0]['size'] = value
    assert errors(data) == ['shots[0].elements[0].size: must be a finite number above 0 and at most 50']


@pytest.mark.parametrize('value', ['#F2B13', 'f2b134', '#f2b13g', '#f2b1340', 'orange', 'rgb(1,2,3)', None, 0xf2b134])
def test_colours_are_hex(value):
    data = board()
    data['shots'][0]['elements'][0]['color'] = value
    data['background'] = value
    assert errors(data) == ['background: must be a hex colour like #1a2b3c',
                            'shots[0].elements[0].color: must be a hex colour like #1a2b3c']


def test_upper_case_hex_is_a_colour():
    data = board()
    data['background'] = '#0B0D10'
    assert errors(data) == []


@pytest.mark.parametrize('text, ok', [('D', True), ('x' * 60, True), ('Dream — v0.3 ✦', True), ('', False),
                                      ('x' * 61, False), ('two\nlines', False), ('bell\x07', False), (None, False),
                                      (60, False)])
def test_text_is_1_to_60_printable_characters(text, ok):
    data = board()
    data['shots'][3]['elements'] = [element('text', text=text)]
    assert errors(data) == ([] if ok else ['shots[3].elements[0].text: must be 1 to 60 printable characters'])


def test_only_a_text_element_has_text_and_a_text_element_needs_it():
    data = board()
    data['shots'][0]['elements'] = [element('sphere', text='Hello')]
    assert errors(data) == ['shots[0].elements[0].text: only a text element has text']
    data['shots'][0]['elements'] = [{k: v for k, v in element('text').items() if k != 'text'}]
    assert errors(data) == ['shots[0].elements[0].text: missing']


@pytest.mark.parametrize('kind', TYPES)
def test_the_six_element_types(kind):
    data = board()
    data['shots'][0]['elements'] = [element(kind)]
    assert errors(data) == []


@pytest.mark.parametrize('kind', ['cube', 'Sphere', 'light', '', None])
def test_other_element_types_are_refused(kind):
    data = board()
    data['shots'][0]['elements'] = [element('sphere', type=kind)]
    assert errors(data) == ['shots[0].elements[0].type: must be one of sphere, ring, disc, plane, text, figure']


def test_at_most_8_elements_per_shot_and_none_is_allowed():
    data = board()
    data['shots'][0]['elements'] = [element() for _ in range(8)]
    data['shots'][1]['elements'] = []
    assert errors(data) == []
    data['shots'][0]['elements'].append(element())
    assert errors(data) == ['shots[0].elements: must be a list of at most 8 elements']


@pytest.mark.parametrize('fade_in, fade_out, ok', [(0, 0, True), (12, 0, True), (0, 71, True), (40, 31, True),
                                                   (71, 0, True), (40, 32, False), (72, 0, False), (0, 72, False)])
def test_fades_leave_the_shots_last_frame(fade_in, fade_out, ok):
    data = board()
    data['shots'][0].update(fade_in=fade_in, fade_out=fade_out)          # shot 0 has 72 frames
    assert errors(data) == ([] if ok else ['shots[0]: fade_in + fade_out must be at most frames - 1 (71)'])


@pytest.mark.parametrize('value', [-1, 1.5, '6', True, None, float('nan')])
def test_a_fade_is_a_whole_number_of_frames(value):
    data = board()
    data['shots'][0]['fade_out'] = value
    assert errors(data) == ['shots[0].fade_out: must be a whole number, 0 or more']


@pytest.mark.parametrize('where, key', [
    ((), 'title'),
    (('shots', 0), 'transition'),
    (('shots', 0, 'camera'), 'lens'),
    (('shots', 0, 'elements', 0), 'rotation'),
])
def test_unknown_keys_are_refused_at_every_level(where, key):
    data = board()
    node = data
    for part in where:
        node = node[part]
    node[key] = 1
    path = ''.join(f'[{p}]' if isinstance(p, int) else f'.{p}' for p in where).lstrip('.')
    assert errors(data) == [f'{path + "." if path else ""}{key}: unknown key']


def test_a_long_blank_key_is_cut_like_a_visible_one():
    data = board()
    data['shots'][0][' ' * 1000] = 1
    [line] = errors(data)
    assert line == 'shots[0]."' + ' ' * 79 + '…: unknown key' and len(line) < 120


@pytest.mark.parametrize('where, key, shown', [
    ((), '', '""'),
    (('shots', 0), ' ', '" "'),
    (('shots', 0, 'elements', 0), '\t', '"\\t"'),
])
def test_a_blank_unknown_key_is_shown_quoted(where, key, shown):
    data = board()
    node = data
    for part in where:
        node = node[part]
    node[key] = 1
    path = ''.join(f'[{p}]' if isinstance(p, int) else f'.{p}' for p in where).lstrip('.')
    assert errors(data) == [f'{path + "." if path else ""}{shown}: unknown key']


@pytest.mark.parametrize('where, key', [
    ((), 'background'),
    (('shots', 1), 'fade_in'),
    (('shots', 1, 'camera'), 'target'),
    (('shots', 1, 'elements', 0), 'color'),
])
def test_missing_keys_are_named(where, key):
    data = board()
    node = data
    for part in where:
        node = node[part]
    del node[key]
    path = ''.join(f'[{p}]' if isinstance(p, int) else f'.{p}' for p in where).lstrip('.')
    assert errors(data) == [f'{path + "." if path else ""}{key}: missing']


@pytest.mark.parametrize('data, line', [
    ([], 'storyboard: must be a JSON object'),
    ({**board(), 'version': 2}, 'version: must be 1'),
    ({**board(), 'version': True}, 'version: must be 1'),
    ({**board(), 'shots': [shot(360), 'shot two']}, 'shots[1]: must be a JSON object'),
    ({**board(), 'shots': [shot(360), shot(360, camera=[0, 0, 0])]}, 'shots[1].camera: must be a JSON object'),
    ({**board(), 'shots': [shot(360), shot(360, elements=['sphere'])]}, 'shots[1].elements[0]: must be a JSON object'),
])
def test_the_shape_is_named(data, line):
    assert line in errors(data)


def test_errors_are_at_most_20_readable_lines():
    data = board(*[60] * 12)
    for item in data['shots']:
        item['elements'] = [element(color='red', size=0) for _ in range(3)]      # 6 errors per shot, 72 in all
    lines = errors(data)
    assert len(lines) == 20
    assert lines[0] == 'shots[0].elements[0].size: must be a finite number above 0 and at most 50'
    assert lines[1] == 'shots[0].elements[0].color: must be a hex colour like #1a2b3c'
    assert lines[19] == '... and 53 more problems'
    assert all(len(line) <= 200 and line.isprintable() for line in lines)
    data['shots'][0]['elements'] = [element(color='red')]                          # 20 errors exactly: all shown
    data['shots'] = data['shots'][:2] + [shot(60, elements=[element(size=0)]) for _ in range(10)]
    data['shots'][1]['elements'] = [element(color='red', size=0) for _ in range(3)]
    data['shots'][2]['elements'] = [element(color='red', size=0) for _ in range(2)]
    lines = errors(data)
    assert len(lines) == 20 and not lines[-1].startswith('...')


def test_a_hostile_key_is_shown_escaped_and_cut():
    data = board()
    data['shots'][0]['\x1b[2J' + 'k' * 300] = 1
    [line] = errors(data)
    assert line.isprintable() and len(line) < 140 and line.endswith(': unknown key') and '\\x1b' in line


# --- plan -----------------------------------------------------------------------------------------------------------

def test_plan_gives_contiguous_1_based_ranges_ending_at_720():
    out = sb().plan(board())
    assert (out['version'], out['fps'], out['width'], out['height'], out['frames']) == (1, 24, 1920, 1080, 720)
    assert [(s['shot'], s['start'], s['end']) for s in out['shots']] == [
        (1, 1, 72), (2, 73, 168), (3, 169, 288), (4, 289, 432), (5, 433, 600), (6, 601, 720)]


def test_plan_gives_camera_and_fade_keyframes():
    data = board()
    data['shots'][1].update(fade_in=12, fade_out=24,
                            camera={'from': [1, -8, 2], 'to': [0, -5.5, 3], 'target': [0, 0, 1]})
    data['shots'][2].update(fade_in=0, fade_out=0)
    data['shots'][3].update(fade_in=100, fade_out=43)                  # 144 frames: the two ramps meet
    out = sb().plan(data)['shots']
    assert out[1]['camera'] == [{'frame': 73, 'location': [1.0, -8.0, 2.0], 'target': [0.0, 0.0, 1.0]},
                                {'frame': 168, 'location': [0.0, -5.5, 3.0], 'target': [0.0, 0.0, 1.0]}]
    assert out[1]['fade'] == [{'frame': 73, 'value': 0.0}, {'frame': 85, 'value': 1.0},
                              {'frame': 144, 'value': 1.0}, {'frame': 168, 'value': 0.0}]
    assert out[2]['fade'] == [{'frame': 169, 'value': 1.0}]
    assert out[3]['fade'] == [{'frame': 289, 'value': 0.0}, {'frame': 389, 'value': 1.0}, {'frame': 432, 'value': 0.0}]
    assert out[0]['elements'] == [{'type': 'sphere', 'at': [0.0, 0.0, 1.0], 'size': 1.5, 'color': '#f2b134'}]


def test_plan_normalises_numbers_and_colours():
    data = board()
    data['background'] = '#0B0D10'
    data['shots'][0]['elements'] = [element('text', at=[1, 2, 3], size=2, color='#ABCDEF', text='Hi')]
    out = sb().plan(data)
    assert out['background'] == '#0b0d10'
    assert out['shots'][0]['elements'] == [{'type': 'text', 'at': [1.0, 2.0, 3.0], 'size': 2.0, 'color': '#abcdef',
                                            'text': 'Hi'}]


def test_plan_refuses_an_invalid_storyboard():
    with pytest.raises(sb().StoryboardError) as caught:
        sb().plan(board(72, 96, 120, 144, 168, 119))
    assert str(caught.value) == "shots: the shots' frames add up to 719; they must add up to exactly 720"


def reordered(value):
    """The same storyboard with every object's keys in reverse order."""
    if isinstance(value, dict):
        return {k: reordered(value[k]) for k in reversed(list(value))}
    if isinstance(value, list):
        return [reordered(v) for v in value]
    return value


def test_the_same_input_gives_byte_identical_json():
    data = board()
    data['shots'][0]['elements'] = [element('ring', at=[1, 2, 3], size=2), element('text', text='Hi')]
    before = copy.deepcopy(data)
    first = json.dumps(sb().plan(data))
    assert data == before                                               # the input is not changed
    assert json.dumps(sb().plan(copy.deepcopy(data))) == first
    assert json.dumps(sb().plan(reordered(data))) == first
    floats = copy.deepcopy(data)
    floats['shots'][0]['elements'][0]['at'] = [1.0, 2.0, 3.0]
    floats['shots'][0]['elements'][0]['size'] = 2.0
    floats['shots'][0]['camera']['from'] = [0.0, -8.0, 2.0]
    assert json.dumps(sb().plan(floats)) == first                        # 1 and 1.0 plan alike, sizes too
    zeros = copy.deepcopy(data)
    zeros['shots'][0]['camera']['from'] = [-0.0, -8, 2]
    zeros['shots'][0]['elements'][0]['at'] = [1, 2, 3]
    zeros['shots'][1]['elements'][0]['at'] = [-0.0, -0.0, 1]
    assert json.dumps(sb().plan(zeros)) == first                        # -0.0 plans as 0.0
    assert '-0.0' not in first


def random_board(rng: random.Random) -> dict:
    count = rng.randint(2, 12)
    frames = [12] * count
    left = 720 - 12 * count
    while left:
        i = rng.choice([n for n, f in enumerate(frames) if f < 360])
        step = min(left, rng.randint(1, 360 - frames[i]))
        frames[i] += step
        left -= step
    rng.shuffle(frames)

    def number(low, high):
        return rng.choice([rng.randint(int(low), int(high)), round(rng.uniform(low, high), rng.randint(0, 6))])

    def point():
        return [number(-100, 100) for _ in range(3)]

    def colour():
        return '#' + ''.join(rng.choice('0123456789abcdefABCDEF') for _ in range(6))

    shots = []
    for n in frames:
        fade_in = rng.randint(0, n - 1)
        fade_out = rng.randint(0, n - 1 - fade_in)
        elements = []
        for _ in range(rng.randint(0, 8)):
            kind = rng.choice(TYPES)
            item = {'type': kind, 'at': point(), 'size': max(0.01, number(0.01, 50)), 'color': colour()}
            if kind == 'text':
                item['text'] = ''.join(rng.choice(string.ascii_letters + ' éß✦—0123456789')
                                       for _ in range(rng.randint(1, 60))).strip() or 'X'
            elements.append(item)
        shots.append({'frames': n, 'camera': {'from': point(), 'to': point(), 'target': point()},
                      'fade_in': fade_in, 'fade_out': fade_out, 'elements': elements})
    return {'version': 1, 'background': colour(), 'shots': shots}


def test_100_random_valid_storyboards_plan_contiguously_and_deterministically():
    rng = random.Random(217)
    for _ in range(100):
        data = random_board(rng)
        assert errors(data) == [], data
        out = sb().plan(data)
        text = json.dumps(out)
        assert json.dumps(sb().plan(copy.deepcopy(data))) == text
        assert json.dumps(sb().plan(reordered(data))) == text
        assert out['frames'] == 720 and out['background'] == data['background'].lower()
        expected_start = 1
        for n, (given, planned) in enumerate(zip(data['shots'], out['shots'], strict=True)):
            assert planned['shot'] == n + 1 and planned['start'] == expected_start
            assert planned['end'] - planned['start'] + 1 == given['frames']
            start, end = planned['start'], planned['end']
            camera = given['camera']
            assert planned['camera'] == [
                {'frame': start, 'location': [float(v) for v in camera['from']],
                 'target': [float(v) for v in camera['target']]},
                {'frame': end, 'location': [float(v) for v in camera['to']],
                 'target': [float(v) for v in camera['target']]}]
            fade = planned['fade']
            frames_of = [k['frame'] for k in fade]
            assert frames_of == sorted(set(frames_of)) and frames_of[0] == start and frames_of[-1] <= end
            assert fade[0]['value'] == (0.0 if given['fade_in'] else 1.0)
            if given['fade_in']:
                assert {'frame': start + given['fade_in'], 'value': 1.0} in fade
            if given['fade_out']:
                assert fade[-1] == {'frame': end, 'value': 0.0}
                assert {'frame': end - given['fade_out'], 'value': 1.0} in fade
            assert all(k['value'] in (0.0, 1.0) for k in fade)
            assert [e['type'] for e in planned['elements']] == [e['type'] for e in given['elements']]
            expected_start = end + 1
        assert expected_start == 721
