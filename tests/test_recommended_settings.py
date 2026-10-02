"""DREAM-179: the model card's recommended sampling per model and mode, as load-screen defaults and a snapshot."""
from types import SimpleNamespace

import pytest

from test_model_presets import gguf
from dream.local.model_defaults import recommend
from dream.desktop.protocol import refill
from dream.local.settings import session_options
from dream.core.backends.openai_compat import OpenAICompatBackend

SAMPLING = ['temperature', 'top_k', 'top_p', 'min_p', 'repeat_penalty', 'presence_penalty', 'frequency_penalty',
            'max_tokens']
HW = {'devices': [{'name': 'B70', 'vram_total_mib': 32768, 'integrated': False}] * 2, 'ram_total_gb': 256}


QWEN38_TEMPLATE = '<think> Reasoning effort is set to xhigh.'
IDENTITY = {   # the six carded models' header identity (names and templates as in the owner's local files)
    'qwen35': {'general.name': 'Qwen3.8-27B', 'tokenizer.chat_template': QWEN38_TEMPLATE},
    'qwen4exp': {'general.name': 'Qwen3.8 Flash Next', 'tokenizer.chat_template': QWEN38_TEMPLATE},
    'qwen35moe': {'general.name': 'Ours', 'general.base_model.0.repo_url': 'https://huggingface.co/Qwen/Qwen3.6-35B-A3B',
                  'tokenizer.chat_template': '<think>'},
    'glm5next': {'general.name': 'GLM 5.3 Flash'},
    'mimo2': {'general.name': 'MiMo-V2.6-Flash-RL'},
}


def rec(tmp_path, arch, *, thinking=True, efforts=(), extra=None, identity=None):
    header = IDENTITY.get(arch, {}) if identity is None else identity
    path = gguf(tmp_path / 'm.gguf', {'general.architecture': arch, f'{arch}.context_length': 262144, **header})
    caps = {'architecture': arch, 'sampling': SAMPLING, 'load': ['thinking'] + (['reasoning_effort'] if efforts else []),
            'defaults': {'temperature': .7, 'top_k': 40, 'top_p': .95, 'min_p': 0., 'repeat_penalty': 1.,
                         'presence_penalty': 0., 'frequency_penalty': 0., 'max_tokens': 16384, 'thinking': thinking},
            'reasoning': {'effort_levels': list(efforts), 'default_effort': efforts[-1] if efforts else ''},
            **(extra or {})}
    return recommend(path, caps, hardware=HW)


def test_qwen_thinking_and_instruct_snapshot_lines_and_defaults(tmp_path):
    r = rec(tmp_path, 'qwen35', efforts=('low', 'medium', 'high', 'xhigh'))
    o, modes = r['options'], r['modes']
    assert (o['temperature'], o['top_p'], o['top_k'], o['min_p'], o['presence_penalty']) == (1.0, .95, 20, 0, 0)
    assert o['max_tokens'] == 16384                     # the card's 256K output is shown, never pinned (PLAN 3.2-8)
    assert modes['thinking']['line'] == ('Thinking — temp 1.0 · top_p 0.95 · top_k 20 · presence 0 · '
                                         'max output 256K reasoning / 128K answer')
    assert modes['instruct']['line'] == 'Instruct — temp 0.7 · top_p 0.8 · top_k 20 · presence 1.5'
    assert 'coding' in modes['thinking']['when'] and 'faster' in modes['instruct']['when']
    assert set(modes['effort']) == {'xhigh', 'high', 'medium', 'low'}   # the engine offers high (= xhigh)
    assert 'huggingface.co/Qwen/Qwen3.8-27B' in r['sources']['temperature']
    assert r['modes']['active'] == 'thinking'


def test_thinking_off_default_applies_instruct_values(tmp_path):
    o = rec(tmp_path, 'qwen4exp', thinking=False)['options']
    assert (o['temperature'], o['top_p'], o['presence_penalty']) == (.7, .8, 1.5)


def test_glm_has_effort_only_top_p_095_and_unofficial_thinking_off(tmp_path):
    r = rec(tmp_path, 'glm5next', efforts=('low', 'high', 'max'))
    assert (r['options']['temperature'], r['options']['top_p'], r['options']['top_k']) == (1.0, .95, 0)
    assert r['modes']['instruct'] is None
    assert set(r['modes']['effort']) == {'max', 'high', 'low'}
    assert 'unofficial' in r['sources']['thinking'] and 'unofficial' in r['modes']['note']
    assert not any('coding starting profile' in note for note in r['notes'])


@pytest.mark.parametrize('arch,temp,top_p', [('qwen35moe', .6, .95), ('deepseek_v41', 1.0, .95),
                                              ('mimo_v2', 1.0, .95), ('mimo2', 1.0, .95)])
def test_owner_decisions_and_other_models(tmp_path, arch, temp, top_p):
    r = rec(tmp_path, arch)
    assert (r['options']['temperature'], r['options']['top_p']) == (temp, top_p)
    assert r['modes']['instruct'] is not None


def test_distill_instruct_carries_the_publisher_caveat(tmp_path):
    assert 'did not evaluate' in rec(tmp_path, 'qwen35moe')['modes']['instruct']['when']


def test_engine_recommended_block_wins_over_the_built_in_table(tmp_path):
    block = {'thinking': {'temp': .5, 'top_p': .9, 'top_k': 10, 'presence': 0, 'when': 'engine said'},
             'instruct': None, 'effort': ['max'], 'source_url': 'https://example.invalid/card'}
    r = rec(tmp_path, 'qwen35', extra={'recommended': block})
    assert (r['options']['temperature'], r['options']['top_p'], r['options']['top_k']) == (.5, .9, 10)
    assert r['modes']['thinking']['when'] == 'engine said' and r['modes']['instruct'] is None
    assert r['modes']['effort'] == {}                   # a malformed effort field is dropped, not fatal
    assert 'example.invalid/card' in r['sources']['temperature']


def test_engine_null_block_means_no_recommendation(tmp_path):
    r = rec(tmp_path, 'qwen35', extra={'recommended': None})       # a new engine: nothing carded for this model
    assert r['modes'] is None and r['options']['temperature'] == .7 and r['options']['top_k'] == 40
    assert not any('Model card' in str(source) for source in r['sources'].values())


@pytest.mark.parametrize('arch,header', [
    ('qwen35', {'general.name': 'Qwen_Qwen3.5 9B', 'tokenizer.chat_template': '<think>'}),
    ('qwen35', {'general.name': 'Bonsai-27B', 'tokenizer.chat_template': '<think> preserve_thinking'}),
    ('qwen35', {'general.name': 'Qwen3.6 27b Aspa N2', 'tokenizer.chat_template': '<think>',
                'general.base_model.0.repo_url': 'https://huggingface.co/Qwen/Qwen3.6-27B'}),
    ('qwen35moe', {'general.name': 'Qwen_Qwen3.6 35B A3B', 'tokenizer.chat_template': '<think>'}),
    ('qwen35moe', {'general.name': 'Ours', 'tokenizer.chat_template': '<think>'}),   # no Qwen3.6-35B-A3B base
    ('qwen4exp', {'general.name': 'Qwen3.8 Flash Next'}),                            # no Qwen3.8 template
    ('glm5next', {'general.name': 'GLM 5.2'}),
    ('mimo2', {'general.name': 'MiMo-V2-Flash'}),
    ('qwen35', {}),                                                                  # unreadable identity
])
def test_uncarded_models_get_no_built_in_recommendation(tmp_path, arch, header):
    r = rec(tmp_path, arch, identity=header)
    assert r['modes'] is None and r['options']['temperature'] == .7 and r['options']['top_k'] == 40
    assert not any('Model card' in str(source) for source in r['sources'].values())


def test_unknown_architecture_keeps_backend_defaults_and_no_snapshot(tmp_path):
    r = rec(tmp_path, 'llama')
    assert r['options']['temperature'] == .7 and r['modes'] is None


def test_mode_switch_refills_only_untouched_fields(tmp_path):
    modes = rec(tmp_path, 'qwen35')['modes']
    current = {'temperature': 1.0, 'top_p': .95, 'top_k': 33, 'presence_penalty': 0.}   # top_k was edited
    changed = refill(current, modes['thinking']['values'], modes['instruct']['values'])
    assert changed == {'temperature': .7, 'top_p': .8, 'presence_penalty': 1.5}


def _backend():
    return OpenAICompatBackend(provider=SimpleNamespace(key='machx', label='machx', base_url='http://x/v1',
                                                        multimodal=False, api_key=lambda: 'n'),
                               model='fixture', system_prompt='s', tools=[], permission_cb=None)


def test_local_launch_never_sends_dream_default_frequency_penalty():
    with session_options({'temperature': 1.0, 'presence_penalty': 1.5}, 'fixture'):
        sampling = _backend()._sampling
    assert 'frequency_penalty' not in sampling and sampling['presence_penalty'] == 1.5
    with session_options({'frequency_penalty': .2}, 'fixture'):
        assert _backend()._sampling['frequency_penalty'] == .2


def test_local_launch_keeps_the_users_own_frequency_penalty(monkeypatch):
    import dream.config as config
    monkeypatch.setenv('DREAM_FREQUENCY_PENALTY', '0.4')
    monkeypatch.setattr(config, 'FREQUENCY_PENALTY', .4)
    with session_options({'temperature': 1.0}, 'fixture'):
        assert _backend()._sampling['frequency_penalty'] == .4


@pytest.mark.asyncio
async def test_terminal_editor_refills_unedited_rows_on_a_thinking_toggle(tmp_path, monkeypatch):
    from io import StringIO
    from rich.console import Console
    from dream.local import launcher
    r = rec(tmp_path, 'qwen35')
    caps = {'sampling': SAMPLING, 'load': ['thinking'],
            'defaults': {**r['options']}}
    answers = iter(['top_k', '33', 'thinking', 'off', ''])
    async def ask(_, **kwargs):
        return next(answers)
    monkeypatch.setattr(launcher, '_ask', ask)
    out = await launcher._edit_options(Console(file=StringIO(), width=160), SimpleNamespace(system=lambda _: None,
                                       error=lambda _: None), caps, 8192, initial=dict(r['options']), modes=r['modes'])
    assert out['thinking'] is False and (out['temperature'], out['top_p'], out['presence_penalty']) == (.7, .8, 1.5)
    assert out['top_k'] == 33                            # the user's edit is kept
