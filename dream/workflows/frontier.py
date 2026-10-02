"""The Frontier review loop's pure rules (DREAM-216, ADR-070): its templates and their refusals, the judges' score
replies, the scores, the token projection and the decision after each round. Nothing here calls a model or writes a
file; the only read is the built-in templates file beside this module."""
from __future__ import annotations

from decimal import ROUND_HALF_UP, Decimal
import json
from pathlib import Path
import re

from ..core.council_config import validate_model
from .storyboard import fences, shown_key

BUILTINS = Path(__file__).with_name('frontier_templates.json')
PROVIDERS = ('anthropic', 'codex', 'grok', 'openai', 'xai', 'machx')
RENDER = {'width': 1920, 'height': 1080, 'fps': 24, 'seconds': 30}   # all the storyboard's schema v1 renders
RUN_SECONDS = 30 * 60        # a run's time limit, the render not counted
NOTE_CHARS = 600
REPLY_CHARS = 32_768         # a longer judge reply is refused rather than searched

# The expected tokens of a step that has not run yet in this run: the plan's section 4 upper estimates per call,
# constants rather than template fields (the lead's ruling of 2026-09-30). A judge round's default is the sum over
# its judges.
CREATE_TOKENS = 10_000
REVISE_TOKENS = 13_000
JUDGE_TOKENS = {'anthropic': 4_000, 'codex': 14_000, 'grok': 29_000,
                'openai': 4_000, 'xai': 4_000, 'machx': 4_000}    # openai, xai and machx: unmeasured

# The decision after a round.
STOP_JUDGES = 'stop_judges'      # undecided: fewer valid judges than the round needs; the owner is needed
RENDER_READY = 'render_ready'    # the shown average reached the threshold; the owner is needed to render
STOP_ROUNDS = 'stop_rounds'      # the rounds are used up: never a revision after the last judging
STOP_TOKENS = 'stop_tokens'      # the next step's projection is over the cap
STOP_TIME = 'stop_time'          # the run is over its time limit
REVISE = 'revise'

_TEMPLATE = ('name', 'create', 'judges', 'rubric', 'stop', 'render', 'brief')
_PERSONA = ('name', 'provider', 'model', 'voice')
_CRITERION = ('key', 'label')
_STOP = ('threshold', 'rounds', 'max_tokens')
_KEY = re.compile(r'[a-z][a-z0-9_]{0,31}\Z')
_DECODER = json.JSONDecoder()


class TemplateError(ValueError):
    """A refused template: `key` names the entry (as council_config.ConfigError does), and the text is shown as it
    is."""

    def __init__(self, key: str, text: str):
        super().__init__(text)
        self.key = key


class ParseError(ValueError):
    """A judge reply that holds no usable score object."""


def builtin_templates() -> list[dict]:
    """The shipped templates, each validated; a broken built-in is a packaging error and raises."""
    data = json.loads(BUILTINS.read_text(encoding='utf-8'))
    if not isinstance(data, dict) or set(data) != {'version', 'templates'} or data['version'] != 1:
        raise ValueError(f'{BUILTINS.name} must hold version 1 and its templates')
    return [validate_template(template) for template in data['templates']]


def validate_template(data: object) -> dict:
    """A new, normalised copy of a valid template, or TemplateError for the first entry refused (at each level,
    unknown keys first, then missing ones, then each value in order)."""
    data = _object(data, 'template', _TEMPLATE)
    out = {'name': _name(data['name'], 'name'), 'create': _persona(data['create'], 'create')}
    judges = data['judges']
    if not isinstance(judges, list) or not 1 <= len(judges) <= 5:
        raise TemplateError('judges', 'Choose 1 to 5 judges')
    out['judges'], seen = [], set()
    for n, judge in enumerate(judges):
        judge = _persona(judge, f'judges[{n}]')
        if judge['name'].casefold() in seen:
            raise TemplateError(f'judges[{n}].name', 'Judges must have different names')
        seen.add(judge['name'].casefold())
        out['judges'].append(judge)
    rubric = data['rubric']
    if not isinstance(rubric, list) or not 1 <= len(rubric) <= 8:
        raise TemplateError('rubric', 'A rubric must list 1 to 8 criteria')
    out['rubric'], keys = [], set()
    for n, criterion in enumerate(rubric):
        criterion = _object(criterion, f'rubric[{n}]', _CRITERION)
        key = criterion['key']
        if not isinstance(key, str) or not _KEY.match(key) or key == 'note':
            raise TemplateError(f'rubric[{n}].key', 'A criterion key must be 1 to 32 lowercase letters, digits or '
                                'underscores, starting with a letter, and not note')
        if key in keys:
            raise TemplateError(f'rubric[{n}].key', 'Criteria must have different keys')
        keys.add(key)
        out['rubric'].append({'key': key, 'label': _name(criterion['label'], f'rubric[{n}].label')})
    stop = _object(data['stop'], 'stop', _STOP)
    threshold = stop['threshold']
    if type(threshold) not in (int, float) or not 1 <= threshold <= 10:     # NaN and the infinities fail the range
        raise TemplateError('stop.threshold', 'The threshold must be a number from 1 to 10')
    if type(stop['rounds']) is not int or not 1 <= stop['rounds'] <= 5:
        raise TemplateError('stop.rounds', 'Rounds must be a whole number from 1 to 5')
    if type(stop['max_tokens']) is not int or not 10_000 <= stop['max_tokens'] <= 1_000_000:
        raise TemplateError('stop.max_tokens', 'The token cap must be a whole number from 10,000 to 1,000,000')
    out['stop'] = {'threshold': float(threshold), 'rounds': stop['rounds'], 'max_tokens': stop['max_tokens']}
    render = _object(data['render'], 'render', tuple(RENDER))
    for key, value in RENDER.items():
        if type(render[key]) is not int or render[key] != value:
            raise TemplateError(f'render.{key}', 'Render settings are fixed in this version: 1920x1080, 24 fps, '
                                '30 seconds')
    out['render'] = dict(RENDER)
    if not isinstance(data['brief'], str) or len(data['brief']) > 2000:
        raise TemplateError('brief', 'A brief must be text of at most 2,000 characters')
    out['brief'] = data['brief']
    return out


def _object(value: object, key: str, keys: tuple[str, ...]) -> dict:
    if not isinstance(value, dict):
        raise TemplateError(key, 'Must be a JSON object')
    prefix = '' if key == 'template' else key + '.'
    unknown = [name for name in value if name not in keys]
    if unknown:
        raise TemplateError(', '.join(sorted(prefix + shown_key(name) for name in unknown)), 'Unknown key')
    for name in keys:
        if name not in value:
            raise TemplateError(prefix + name, 'Missing key')
    return value


def _name(value: object, key: str) -> str:
    text = value.strip() if isinstance(value, str) else ''
    if not 1 <= len(text) <= 80 or not text.isprintable():
        raise TemplateError(key, 'A name must be printable text of 1 to 80 characters')
    return text


def _persona(value: object, key: str) -> dict:
    value = _object(value, key, _PERSONA)
    name = _name(value['name'], f'{key}.name')
    if not isinstance(value['provider'], str) or value['provider'] not in PROVIDERS:
        raise TemplateError(f'{key}.provider', 'Unknown provider; choose anthropic, codex, grok, openai, xai or machx')
    try:
        model = validate_model(value['model'], allow_empty=True)     # empty: the provider's own default
    except ValueError as exc:
        raise TemplateError(f'{key}.model', str(exc)) from None
    if not isinstance(value['voice'], str) or len(value['voice']) > 2000:
        raise TemplateError(f'{key}.voice', 'A voice must be text of at most 2,000 characters')
    return {'name': name, 'provider': value['provider'], 'model': model, 'voice': value['voice']}


def parse_judge_reply(text: object, keys: list[str]) -> dict:
    """{'scores': {key: int}, 'note': str} from a judge's reply, or ParseError (and nothing else). A json code fence
    is read in preference to the prose around it, the last fence if there are several; within what is read, the
    last JSON object counts. Every rubric key and `note` are required and nothing else is allowed; each score is an
    integer from 1 to 10; the note has its whitespace collapsed and is cut to 600 characters."""
    if not isinstance(text, str):
        raise ParseError('The reply is not text')
    if len(text) > REPLY_CHARS:
        raise ParseError('The reply is longer than 32,768 characters')
    bodies = fences(text)[0]
    found = _objects(bodies[-1] if bodies else text)
    if not found:
        raise ParseError('The reply holds no JSON object')
    answer = found[-1]
    missing = [key for key in [*keys, 'note'] if key not in answer]
    if missing:
        raise ParseError('Missing: ' + ', '.join(missing))
    extra = sorted(shown_key(key) for key in answer if key not in keys and key != 'note')
    if extra:
        raise ParseError('Not in the rubric: ' + ', '.join(extra))
    scores = {}
    for key in keys:
        if type(answer[key]) is not int or not 1 <= answer[key] <= 10:
            raise ParseError(f'{key} must be a whole number from 1 to 10')
        scores[key] = answer[key]
    note = ' '.join(answer['note'].split()) if isinstance(answer['note'], str) else ''
    if not note:
        raise ParseError('The note must be text that is not empty')
    note = ''.join(c if c.isprintable() else '\ufffd' for c in note)     # controls, format marks, lone surrogates
    if len(note) > NOTE_CHARS:
        note = note[:NOTE_CHARS - 1] + '…'
    return {'scores': scores, 'note': note}


def _objects(text: str) -> list[dict]:
    """Every top-level JSON object in the text, left to right. A decoded object is passed over whole, so braces
    inside it (in a note) never count on their own."""
    found = []
    start = text.find('{')
    while start != -1:
        try:
            value, end = _DECODER.raw_decode(text, start)
        except (ValueError, RecursionError):      # not JSON here, too deep, or a number past the conversion limit
            start = text.find('{', start + 1)
            continue
        found.append(value)
        start = text.find('{', end)
    return found


def judge_score(scores: dict[str, int]) -> Decimal:
    """A judge's score: the mean of its criteria, rounded half-up to one decimal."""
    return _half_up([Decimal(value) for value in scores.values()])


def round_average(scores: list[Decimal | None]) -> Decimal | None:
    """A round's average from one entry per judge (None for a judge without a valid score): the mean of the scores
    as shown, rounded half-up to one decimal; None, undecided, when fewer than min(2, judges) are valid (the lead's
    ruling of 2026-09-30: one judge decides alone, two need both, three or more need two)."""
    valid = [score for score in scores if score is not None]
    if len(valid) < min(2, len(scores)):
        return None
    return _half_up(valid)


def _half_up(values: list[Decimal]) -> Decimal:
    return (sum(values, Decimal(0)) / len(values)).quantize(Decimal('0.1'), rounding=ROUND_HALF_UP)


def round_tokens(judges: list[dict]) -> int:
    """A judge round's default expected tokens: the sum of its judges' per-call defaults."""
    return sum(JUDGE_TOKENS[judge['provider']] for judge in judges)


def expected_tokens(default: int, spent: tuple[int, ...] | list[int] = ()) -> int:
    """A step's expected tokens: the larger of its default and the most that step has cost so far in this run."""
    return max([default, *spent])


def before_step(used: int, expected: int, cap: int) -> str | None:
    """STOP_TOKENS when the tokens used plus the step's expected tokens are over the cap: the run stops before the
    step starts. None otherwise."""
    return STOP_TOKENS if used + expected > cap else None


def decide(*, average: Decimal | None, round_no: int, rounds: int, threshold: float, projected: int, cap: int,
           elapsed: float, limit: float = RUN_SECONDS) -> str:
    """The decision after a round, checked in this order (the plan's section 2 as the lead ruled on 2026-09-30): an
    undecided round (STOP_JUDGES, also on the last round); the shown average at or above the threshold
    (RENDER_READY); the rounds used up (STOP_ROUNDS); `projected`, the tokens used plus the revision's expected
    tokens, over the cap (STOP_TOKENS); the run over its time limit (STOP_TIME); else REVISE. The owner's Stop is
    the runner's, at any time."""
    if average is None:
        return STOP_JUDGES
    if average >= Decimal(str(threshold)):
        return RENDER_READY
    if round_no >= rounds:
        return STOP_ROUNDS
    if projected > cap:
        return STOP_TOKENS
    if elapsed > limit:
        return STOP_TIME
    return REVISE
