"""DREAM-216, the Frontier review loop's core rules (F1; ADR-070): the built-in templates, the refusals with their exact
texts, the judges' score replies, the scores and the decision after each round. Pure code: no model, no GPU, nothing
written; the only file read is the shipped templates file."""
from __future__ import annotations

import copy
from decimal import Decimal as D
import importlib
import json
import random

import pytest

KEYS = ['hook_3s', 'brand_fit', 'blender_renderable', 'clarity']

THRESHOLD = 'The threshold must be a number from 1 to 10'
ROUNDS = 'Rounds must be a whole number from 1 to 5'
CAP = 'The token cap must be a whole number from 10,000 to 1,000,000'
JUDGES = 'Choose 1 to 5 judges'
DUPLICATE = 'Judges must have different names'
PROVIDER = 'Unknown provider; choose anthropic, codex, grok, openai, xai or machx'
MODEL = ('Model must be a string of at most 256 characters of printable text (no control, invisible or unencodable '
         'characters)')
VOICE = 'A voice must be text of at most 2,000 characters'
UNKNOWN = 'Unknown key'
MISSING = 'Missing key'
OBJECT = 'Must be a JSON object'
NAME = 'A name must be printable text of 1 to 80 characters'
RUBRIC = 'A rubric must list 1 to 8 criteria'
CRITERION = 'A criterion key must be 1 to 32 lowercase letters, digits or underscores, starting with a letter, and not note'
CRITERIA = 'Criteria must have different keys'
RENDER = 'Render settings are fixed in this version: 1920x1080, 24 fps, 30 seconds'
BRIEF = 'A brief must be text of at most 2,000 characters'


def short(value):
    """A readable test id for a long string or integer parameter (pytest's default id repeats the whole value)."""
    if isinstance(value, str) and len(value) > 40:
        return f'{value[:8]!r}...x{len(value)}'
    if type(value) is int and len(str(value)) > 40:
        return f'int-of-{len(str(abs(value)))}-digits'
    return None


def fr():
    """The module under test, imported in each test so that each one fails on its own while the module is missing."""
    return importlib.import_module('dream.workflows.frontier')


def persona(name, provider, model='', voice='Judge it as yourself.'):
    return {'name': name, 'provider': provider, 'model': model, 'voice': voice}


def template():
    """A valid template written here, independent of the shipped file."""
    return {
        'name': 'Test loop',
        'create': persona('Writer', 'anthropic', 'claude-fable-5-1', 'Write the piece.'),
        'judges': [persona('Judge A', 'anthropic', 'claude-opus-5-5'), persona('Judge B', 'codex'),
                   persona('Judge C', 'grok')],
        'rubric': [{'key': key, 'label': key.replace('_', ' ')} for key in KEYS],
        'stop': {'threshold': 8.0, 'rounds': 3, 'max_tokens': 600_000},
        'render': {'width': 1920, 'height': 1080, 'fps': 24, 'seconds': 30},
        'brief': '',
    }


def changed(path, value):
    data = template()
    *parent, last = path
    node = data
    for part in parent:
        node = node[part]
    node[last] = value
    return data


def refusal(data):
    with pytest.raises(fr().TemplateError) as caught:
        fr().validate_template(data)
    return caught.value.key, str(caught.value)


# --- the built-in templates -----------------------------------------------------------------------------------------

def test_the_builtin_template_validates_and_matches_the_mockup():
    loop = fr().builtin_templates()[0]
    assert loop['name'] == 'Frontier review loop'
    create = loop['create']
    assert (create['name'], create['provider'], create['model']) == ('Brand storyteller', 'anthropic', 'claude-fable-5-1')
    assert [(j['name'], j['provider'], j['model']) for j in loop['judges']] == [
        ('Creative director', 'anthropic', 'claude-opus-5-5'),
        ('Motion designer', 'codex', ''),            # the Codex CLI's own default model
        ('Sceptical viewer', 'grok', '')]
    assert [c['key'] for c in loop['rubric']] == KEYS
    assert [c['label'] for c in loop['rubric']] == ['Hook in 3 seconds', 'Brand fit', 'Can Blender render it', 'Clarity']
    assert loop['stop'] == {'threshold': 8.0, 'rounds': 3, 'max_tokens': 600_000}
    assert loop['render'] == {'width': 1920, 'height': 1080, 'fps': 24, 'seconds': 30}
    assert loop['brief'] == ''
    for who in [create, *loop['judges']]:
        assert who['voice'].strip() and len(who['voice']) <= 2000
    assert fr().validate_template(copy.deepcopy(loop)) == loop


def test_the_quick_template_is_the_same_loop_with_two_rounds_and_a_200k_cap():
    loop, quick = fr().builtin_templates()
    assert quick['name'] == 'Quick review loop'
    assert quick['stop'] == {'threshold': 8.0, 'rounds': 2, 'max_tokens': 200_000}
    assert {**quick, 'name': loop['name'], 'stop': loop['stop']} == loop
    assert fr().validate_template(copy.deepcopy(quick)) == quick


def test_the_shipped_file_holds_exactly_the_two_templates():
    data = json.loads(fr().BUILTINS.read_text(encoding='utf-8'))
    assert set(data) == {'version', 'templates'} and data['version'] == 1
    assert [t['name'] for t in data['templates']] == ['Frontier review loop', 'Quick review loop']


# --- refusals: the key and the exact text ---------------------------------------------------------------------------

@pytest.mark.parametrize('path, value, key, text', [
    *[(('stop', 'threshold'), v, 'stop.threshold', THRESHOLD)
      for v in (0.99, 10.01, 0, 11, -8, '8', True, None, float('nan'), float('inf'),
                10 ** 400, -(10 ** 400), 10 ** 309)],             # integers too large for a float, as JSON decodes them
    *[(('stop', 'rounds'), v, 'stop.rounds', ROUNDS) for v in (0, 6, 3.0, '3', True, None, 10 ** 400)],
    *[(('stop', 'max_tokens'), v, 'stop.max_tokens', CAP)
      for v in (9_999, 1_000_001, 600_000.0, '600000', True, 10 ** 400, -(10 ** 400))],
    (('render', 'fps'), 10 ** 400, 'render.fps', RENDER),
    *[(('judges',), v, 'judges', JUDGES)
      for v in ([], [persona(f'Judge {n}', 'grok') for n in range(6)], 'Judge A', None, {'name': 'Judge A'})],
    *[(('judges', 0, 'provider'), v, 'judges[0].provider', PROVIDER) for v in ('gemini', 'Codex', 'claude', '', None)],
    (('create', 'provider'), 'gemini', 'create.provider', PROVIDER),
    *[(('judges', 1, 'model'), v, 'judges[1].model', MODEL) for v in ('gpt\x1b[2J', 'm' * 257, 5, None)],
    (('create', 'model'), 'claude‮fable', 'create.model', MODEL),
    *[(('create', 'voice'), v, 'create.voice', VOICE) for v in ('v' * 2001, 5, None)],
    (('judges', 2, 'voice'), 'v' * 2001, 'judges[2].voice', VOICE),
], ids=short)
def test_each_refusal_names_the_key_and_gives_the_exact_text(path, value, key, text):
    assert refusal(changed(path, value)) == (key, text)


def test_duplicate_judges_are_the_same_name_ignoring_case_and_spaces():
    data = changed(('judges', 2, 'name'), '  judge a ')
    assert refusal(data) == ('judges[2].name', DUPLICATE)
    data = changed(('judges', 1), persona('Judge A', 'grok', '', 'Another voice'))   # a different model is no excuse
    assert refusal(data) == ('judges[1].name', DUPLICATE)


@pytest.mark.parametrize('path, key', [
    (('colour',), 'colour'),
    (('stop', 'minutes'), 'stop.minutes'),
    (('create', 'effort'), 'create.effort'),
    (('judges', 1, 'temperature'), 'judges[1].temperature'),
    (('rubric', 0, 'weight'), 'rubric[0].weight'),
    (('render', 'samples'), 'render.samples'),
])
def test_unknown_keys_are_refused_at_every_level(path, key):
    assert refusal(changed(path, 1)) == (key, UNKNOWN)


@pytest.mark.parametrize('path, key', [
    (('',), '""'),
    (('stop', ''), 'stop.""'),
    (('judges', 0, ' '), 'judges[0]." "'),
    (('rubric', 1, '\t'), 'rubric[1]."\\t"'),
])
def test_a_blank_unknown_key_is_shown_quoted(path, key):
    assert refusal(changed(path, 1)) == (key, UNKNOWN)


def test_a_long_blank_key_is_cut_like_a_visible_one():
    cut = '"' + ' ' * 79 + '…'                        # json.dumps(' ' * 1000), cut at 80 characters by profiles.shown
    assert refusal(changed(('stop', ' ' * 1000), 1)) == ('stop.' + cut, UNKNOWN)
    with pytest.raises(fr().ParseError) as caught:
        parse(reply(**{' ' * 1000: 5}))
    assert str(caught.value) == 'Not in the rubric: ' + cut


def test_a_model_refusal_is_council_configs_own_text():
    from dream.core.council_config import validate_model
    with pytest.raises(ValueError) as own:
        validate_model('gpt\x1b[2J')
    assert str(own.value) == MODEL
    assert refusal(changed(('judges', 0, 'model'), 'gpt\x1b[2J')) == ('judges[0].model', str(own.value))


@pytest.mark.parametrize('data, key, text', [
    ([], 'template', OBJECT),
    ({k: v for k, v in template().items() if k != 'stop'}, 'stop', MISSING),
    ({**template(), 'create': 'Writer'}, 'create', OBJECT),
    ({**template(), 'judges': [persona('Judge A', 'grok'), 'Judge B']}, 'judges[1]', OBJECT),
    ({**template(), 'judges': [{'name': 'Judge A', 'provider': 'grok', 'model': ''}]}, 'judges[0].voice', MISSING),
    ({**template(), 'stop': [8.0, 3, 600_000]}, 'stop', OBJECT),
    ({**template(), 'name': ''}, 'name', NAME),
    ({**template(), 'name': 'n' * 81}, 'name', NAME),
    ({**template(), 'name': 'Loop\x07'}, 'name', NAME),
    ({**template(), 'create': persona('', 'anthropic')}, 'create.name', NAME),
    ({**template(), 'rubric': []}, 'rubric', RUBRIC),
    ({**template(), 'rubric': [{'key': f'c{n}', 'label': 'x'} for n in range(9)]}, 'rubric', RUBRIC),
    ({**template(), 'rubric': [{'key': 'note', 'label': 'Note'}]}, 'rubric[0].key', CRITERION),
    ({**template(), 'rubric': [{'key': 'Hook', 'label': 'Hook'}]}, 'rubric[0].key', CRITERION),
    ({**template(), 'rubric': [{'key': 'hook', 'label': 'a'}, {'key': 'hook', 'label': 'b'}]}, 'rubric[1].key',
     CRITERIA),
    ({**template(), 'rubric': [{'key': 'hook', 'label': ''}]}, 'rubric[0].label', NAME),
    ({**template(), 'render': {'width': 1920, 'height': 1080, 'fps': 25, 'seconds': 30}}, 'render.fps', RENDER),
    ({**template(), 'render': {'width': 1920.0, 'height': 1080, 'fps': 24, 'seconds': 30}}, 'render.width', RENDER),
    ({**template(), 'brief': 'b' * 2001}, 'brief', BRIEF),
    ({**template(), 'brief': None}, 'brief', BRIEF),
], ids=short)
def test_the_rest_of_the_schema_is_refused_with_exact_texts(data, key, text):
    assert refusal(data) == (key, text)


@pytest.mark.parametrize('path, value', [
    (('stop', 'threshold'), 1), (('stop', 'threshold'), 10), (('stop', 'threshold'), 7.5),
    (('stop', 'rounds'), 1), (('stop', 'rounds'), 5),
    (('stop', 'max_tokens'), 10_000), (('stop', 'max_tokens'), 1_000_000),
    (('judges',), [persona('Only judge', 'machx')]),
    (('judges',), [persona(f'Judge {n}', p) for n, p in enumerate(['anthropic', 'codex', 'grok', 'openai', 'xai'])]),
    (('create', 'voice'), 'v' * 2000), (('create', 'voice'), ''),
    (('judges', 0, 'model'), ''), (('judges', 0, 'model'), 'm' * 256),
    (('brief', ), 'b' * 2000),
], ids=short)
def test_the_edges_of_every_range_are_accepted(path, value):
    fr().validate_template(changed(path, value))


def test_a_valid_template_comes_back_normalised_and_the_input_untouched():
    data = changed(('judges', 0, 'model'), '  claude-opus-5-5 ')
    data['judges'][1]['name'] = '  Judge B '
    data['stop']['threshold'] = 8
    before = copy.deepcopy(data)
    out = fr().validate_template(data)
    assert data == before
    assert out['judges'][0]['model'] == 'claude-opus-5-5' and out['judges'][1]['name'] == 'Judge B'
    assert out['stop']['threshold'] == 8.0 and type(out['stop']['threshold']) is float
    assert list(out) == ['name', 'create', 'judges', 'rubric', 'stop', 'render', 'brief']


# --- the judge's reply ----------------------------------------------------------------------------------------------

def reply(**over):
    obj = {'hook_3s': 7, 'brand_fit': 8, 'blender_renderable': 6, 'clarity': 9, 'note': 'Strong open, weak end.'}
    obj.update(over)
    return json.dumps(obj)


def parse(text):
    return fr().parse_judge_reply(text, KEYS)


def test_bare_json_is_read():
    assert parse(reply()) == {'scores': {'hook_3s': 7, 'brand_fit': 8, 'blender_renderable': 6, 'clarity': 9},
                              'note': 'Strong open, weak end.'}


def test_a_json_fence_is_read():
    assert parse('```json\n' + reply(clarity=3) + '\n```')['scores']['clarity'] == 3


def test_prose_around_the_object_is_read_and_the_last_object_wins():
    text = f'My first take: {reply(clarity=2)}\nOn reflection:\n{reply(clarity=5)}\nThat is all.'
    assert parse(text)['scores']['clarity'] == 5


def test_the_fence_is_read_before_the_prose_around_it():
    # an object in the prose after the fence (an example, a quote) does not replace the fenced answer
    text = ('```json\n' + reply(clarity=4) + '\n```\n'
            'For comparison, last round I would have said {"hook_3s": 2} on its own.')
    assert parse(text)['scores']['clarity'] == 4
    text = '```json\n' + reply(clarity=4) + '\n```\nMy first draft of that was ' + reply(clarity=9)
    assert parse(text)['scores']['clarity'] == 4


@pytest.mark.parametrize('opening, closing', [
    ('```JSON\n', '\n```'),                           # the tag in any case
    ('```Json\n', '\n```'),
    ('  ```json\n', '\n  ```'),                       # an indented fence
    ('\t```json \n', '\n\t```  '),
    ('```json\r\n', '\r\n```\r\n'),                   # CRLF line endings
    ('   ```JSON\r\n', '\r\n   ```\r\n'),
])
def test_a_models_fence_variants_are_read_as_fences(opening, closing):
    # each is read as the fence: the object in the prose after it does not replace the fenced answer
    text = opening + reply(clarity=4) + closing + '\nMy first draft of that was ' + reply(clarity=9)
    assert parse(text)['scores']['clarity'] == 4


@pytest.mark.parametrize('tag', ['jsonc', 'json5', 'JSONC', 'json-schema', 'json x'])
def test_a_tag_that_only_starts_with_json_is_no_fence(tag):
    # read as prose: the last object counts, which here is the one after the would-be fence
    text = f'```{tag}\n' + reply(clarity=4) + '\n```\nMy first draft of that was ' + reply(clarity=9)
    assert parse(text)['scores']['clarity'] == 9


def test_unclosed_fences_up_to_the_cap_are_read_in_linear_time():
    import time
    text = '```json\n' * 4096                              # 32,768 characters, all openings, none closed
    started = time.process_time()                        # CPU time: a starved SCHED_IDLE run cannot fail it
    with pytest.raises(fr().ParseError):
        parse(text)
    assert time.process_time() - started < 0.1           # about 0.4 ms; the regex this replaced took 0.49 s here


def test_the_last_fence_counts_and_inside_it_the_last_object():
    text = ('```json\n' + reply(clarity=1) + '\n```\nRevised:\n```json\n' + reply(clarity=2) + '\n'
            + reply(clarity=6) + '\n```')
    assert parse(text)['scores']['clarity'] == 6


def test_an_object_inside_the_answer_does_not_count_as_the_last_object():
    # '{}' in the note is valid JSON on its own; it lies inside the answer, which is read whole and passed over
    text = reply(note='Cut the empty {} slide.') + ' Done.'
    assert parse(text) == {'scores': {'hook_3s': 7, 'brand_fit': 8, 'blender_renderable': 6, 'clarity': 9},
                           'note': 'Cut the empty {} slide.'}


@pytest.mark.parametrize('value', [0, 11, 7.5, 7.0, '7', True, False, None, -1, [7], {'v': 7}, 10 ** 400], ids=short)
def test_a_score_must_be_an_integer_from_1_to_10(value):
    with pytest.raises(fr().ParseError) as caught:
        parse(reply(brand_fit=value))
    assert str(caught.value) == 'brand_fit must be a whole number from 1 to 10'


def test_1_and_10_are_scores():
    assert parse(reply(hook_3s=1, clarity=10))['scores'] == {'hook_3s': 1, 'brand_fit': 8, 'blender_renderable': 6,
                                                              'clarity': 10}


def test_every_rubric_key_is_required_and_no_other_is_allowed():
    obj = json.loads(reply())
    del obj['clarity'], obj['note']
    with pytest.raises(fr().ParseError) as caught:
        parse(json.dumps(obj))
    assert str(caught.value) == 'Missing: clarity, note'
    with pytest.raises(fr().ParseError) as caught:
        parse(reply(humour=5, overall=7))
    assert str(caught.value) == 'Not in the rubric: humour, overall'


def test_a_blank_key_in_a_reply_is_shown_quoted():
    with pytest.raises(fr().ParseError) as caught:
        parse(reply(**{'': 5, ' ': 6}))
    assert str(caught.value) == 'Not in the rubric: " ", ""'


@pytest.mark.parametrize('note', ['', '   \n\t ', None, 7, ['a']])
def test_the_note_is_required_text(note):
    with pytest.raises(fr().ParseError) as caught:
        parse(reply(note=note))
    assert str(caught.value) == 'The note must be text that is not empty'


@pytest.mark.parametrize('note, shown', [
    ('\ud800 lone surrogate', '\ufffd lone surrogate'),
    ('nul\x00here', 'nul\ufffdhere'),
    ('\x1b[2J clears the screen', '\ufffd[2J clears the screen'),
    ('bidi \u202eesrever', 'bidi \ufffdesrever'),
    ('zero\u200bwidth and\x07bell', 'zero\ufffdwidth and\ufffdbell'),
    ('line one\nline two\r\n\x1c', 'line one line two'),
])
def test_a_notes_unprintable_characters_are_replaced_so_it_always_encodes(note, shown):
    out = parse(reply(note=note))['note']
    assert out == shown
    out.encode('utf-8')
    json.dumps(out, ensure_ascii=False).encode('utf-8')


def test_the_note_has_its_whitespace_collapsed_and_is_capped_at_600_characters():
    assert parse(reply(note='  Cut\n\nshot   3,\tkeep the logo. '))['note'] == 'Cut shot 3, keep the logo.'
    long = parse(reply(note='word ' * 400))['note']
    assert len(long) == 600 and long.endswith('…') and long.startswith('word word')
    exact = 'n' * 600
    assert parse(reply(note=exact))['note'] == exact


@pytest.mark.parametrize('text, message', [
    ('', 'The reply holds no JSON object'),
    ('Scores: hook 7, brand 8.', 'The reply holds no JSON object'),
    ('```json\nnot json\n```', 'The reply holds no JSON object'),
    ('[7, 8, 6, 9]', 'The reply holds no JSON object'),
    (None, 'The reply is not text'),
    (b'{"hook_3s": 7}', 'The reply is not text'),
    (' ' * 32_769, 'The reply is longer than 32,768 characters'),
], ids=short)
def test_a_reply_without_a_usable_object_is_a_parse_error(text, message):
    with pytest.raises(fr().ParseError) as caught:
        parse(text)
    assert str(caught.value) == message


def test_it_raises_only_parse_error_on_200_fuzz_strings():
    rng = random.Random(216)
    tokens = ['{', '}', '[', ']', '"', ':', ',', ' ', '\n', '```json\n', '\n```', '```', '1', '7', '10', '0', '-',
              '.', 'e9', 'true', 'null', 'NaN', 'Infinity', '"hook_3s"', '"brand_fit"', '"blender_renderable"',
              '"clarity"', '"note"', 'a', '\\', '\\u', 'é', '‮', '\x00', '\ud800', '{"a":']
    valid = reply()
    samples = []
    for _ in range(100):                                          # noise built from JSON's own pieces
        samples.append(''.join(rng.choice(tokens) for _ in range(rng.randint(0, 120))))
    for _ in range(100):                                          # a valid reply with a few random edits
        text = list(valid)
        for _ in range(rng.randint(1, 6)):
            at = rng.randrange(len(text) + 1)
            if rng.random() < 0.5 and at < len(text):
                del text[at]
            else:
                text.insert(at, rng.choice(tokens))
        samples.append(''.join(text))
    samples[0] = '{"a":' + '[' * 20_000                           # nesting past the decoder's recursion limit
    samples[1] = '{"hook_3s": 1' + '0' * 5000 + '}'              # an integer past the conversion limit
    samples[2] = '{' * 20_000                                     # many openings, none closed
    assert len(samples) == 200
    parsed = 0
    for text in samples:
        try:
            out = parse(text)
        except fr().ParseError:
            continue
        parsed += 1
        assert list(out) == ['scores', 'note'] and list(out['scores']) == KEYS
        assert all(type(v) is int and 1 <= v <= 10 for v in out['scores'].values())
        assert isinstance(out['note'], str) and 0 < len(out['note']) <= 600
    assert 0 < parsed < 200                                          # both paths are exercised


# --- scores and decisions -------------------------------------------------------------------------------------------

def test_a_judges_score_is_the_mean_of_its_criteria_rounded_half_up():
    score = fr().judge_score
    assert score({'a': 7, 'b': 7, 'c': 7, 'd': 8}) == D('7.3')                  # 7.25: half-up, not half-even
    assert score({'a': 8, 'b': 8, 'c': 8, 'd': 8}) == D('8.0') and str(score({'a': 8, 'b': 8})) == '8.0'
    assert score({'a': 7, 'b': 8, 'c': 8, 'd': 8}) == D('7.8')                  # 7.75
    assert score({'a': 7, 'b': 7, 'c': 8}) == D('7.3')                          # 7.333...
    assert score({'a': 9, 'b': 9, 'c': 8}) == D('8.7')                          # 8.666...


def test_the_mockups_rounds_average_to_6_7_and_7_8():
    average = fr().round_average
    assert average([D('7.2'), D('6.8'), D('6.1')]) == D('6.7')
    assert average([D('8.1'), D('7.9'), D('7.4')]) == D('7.8')


def test_the_round_average_is_of_the_scores_as_shown_and_rounds_half_up():
    score, average = fr().judge_score, fr().round_average
    shown = [score({'a': 7, 'b': 7, 'c': 7, 'd': 8}), score({'a': 8, 'b': 8, 'c': 8, 'd': 8})]
    assert shown == [D('7.3'), D('8.0')]
    assert average(shown) == D('7.7')                    # (7.3 + 8.0) / 2 = 7.65; from the raw means 7.625 it is 7.6
    assert average([D('7.2'), D('7.3')]) == D('7.3')     # 7.25: half-up, not half-even


def test_a_round_needs_min_2_and_its_judge_count_valid_judges():
    average = fr().round_average
    assert average([D('7.0')]) == D('7.0')                           # one judge decides on its own score
    assert average([None]) is None
    assert average([D('7.0'), None]) is None                         # two judges need both
    assert average([D('7.0'), D('6.0')]) == D('6.5')
    assert average([D('7.0'), None, D('6.0')]) == D('6.5')           # three or more need two
    assert average([D('7.0'), None, None]) is None
    assert average([None, D('9.0'), None, None, D('8.0')]) == D('8.5')
    assert average([None, D('9.0'), None, None, None]) is None


def decide(**over):
    args = dict(average=D('7.0'), round_no=1, rounds=3, threshold=8.0, projected=100_000, cap=600_000, elapsed=60.0)
    args.update(over)
    return fr().decide(**args)


def test_a_shown_8_0_goes_to_render_and_7_9_to_revision():
    assert decide(average=D('8.0')) == 'render_ready'
    assert decide(average=D('7.9')) == 'revise'
    exact = fr().round_average([D('7.9'), D('8.0')])                # 7.95 exactly, shown as 8.0
    assert exact == D('8.0') and decide(average=exact) == 'render_ready'
    assert decide(average=D('7.5'), threshold=7.5) == 'render_ready'
    assert decide(average=D('7.4'), threshold=7.5) == 'revise'
    assert decide(average=D('10.0'), threshold=10) == 'render_ready'


def test_the_last_round_never_revises():
    assert decide(average=D('7.9'), round_no=3, rounds=3) == 'stop_rounds'
    assert decide(average=D('1.0'), round_no=1, rounds=1) == 'stop_rounds'
    assert decide(average=D('8.0'), round_no=3, rounds=3) == 'render_ready'


def test_an_undecided_round_stops_with_stop_judges_before_every_other_rule():
    assert decide(average=None) == 'stop_judges'
    assert decide(average=None, round_no=3, rounds=3) == 'stop_judges'      # the lead's ruling: not stop_rounds
    assert decide(average=None, projected=700_000, elapsed=4000.0) == 'stop_judges'


def test_the_rules_after_a_round_are_checked_in_order():
    assert decide(average=D('8.2'), round_no=3, projected=700_000, elapsed=4000.0) == 'render_ready'
    assert decide(average=D('7.0'), round_no=3, projected=700_000, elapsed=4000.0) == 'stop_rounds'
    assert decide(average=D('7.0'), round_no=2, projected=700_000, elapsed=4000.0) == 'stop_tokens'
    assert decide(average=D('7.0'), round_no=2, projected=600_001) == 'stop_tokens'
    assert decide(average=D('7.0'), round_no=2, projected=600_000) == 'revise'          # at the cap is not over it
    assert decide(average=D('7.0'), round_no=2, elapsed=1800.5) == 'stop_time'
    assert decide(average=D('7.0'), round_no=2, elapsed=1800.0) == 'revise'            # at the limit is not over it
    assert fr().RUN_SECONDS == 1800


def test_a_projection_over_the_cap_stops_with_stop_tokens_before_the_step():
    m = fr()
    assert (m.CREATE_TOKENS, m.REVISE_TOKENS) == (10_000, 13_000)
    assert m.JUDGE_TOKENS == {'anthropic': 4_000, 'codex': 14_000, 'grok': 29_000,
                              'openai': 4_000, 'xai': 4_000, 'machx': 4_000}
    loop = m.builtin_templates()[0]
    assert m.round_tokens(loop['judges']) == 47_000                   # 4,000 + 14,000 + 29,000
    assert m.expected_tokens(m.REVISE_TOKENS) == 13_000               # nothing spent yet: the default
    assert m.expected_tokens(m.REVISE_TOKENS, [9_000, 12_500]) == 13_000
    assert m.expected_tokens(m.REVISE_TOKENS, [9_000, 15_200]) == 15_200   # the most that step has cost so far
    assert m.before_step(587_000, m.expected_tokens(m.REVISE_TOKENS), 600_000) is None
    assert m.before_step(587_001, m.expected_tokens(m.REVISE_TOKENS), 600_000) == 'stop_tokens'
    assert m.before_step(560_000, m.expected_tokens(47_000, [52_000]), 600_000) == 'stop_tokens'
    assert m.before_step(0, m.expected_tokens(m.CREATE_TOKENS), 10_000) is None
