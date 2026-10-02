"""Bounded model metadata and sourced local launch recommendations. No model loads."""
from dataclasses import asdict
from pathlib import Path
import struct
import re
from .settings import available_controls

HEADER_LIMIT = 32 * 1024 * 1024

# DREAM-179: the model cards' recommended sampling (sources and quotes: the recsettings PLAN, 2026-09-28). MachX's
# /capabilities `recommended` block wins; this copy serves an older engine. Owner picks: GLM top_p 0.95, the 35B's
# thinking temp 0.6, DS4.1 top_p 0.95. Fields a card leaves out are switched off (NEUTRAL), never guessed.
NEUTRAL = {'top_k': 0, 'min_p': 0., 'presence_penalty': 0., 'repeat_penalty': 1.}
ALIASES = {'temp': 'temperature', 'presence': 'presence_penalty', 'repeat': 'repeat_penalty'}
_QWEN_EFFORT = {'xhigh': 'complex tasks demanding thorough analysis', 'high': 'the same as xhigh (the template maps high to xhigh)',
                'medium': 'balancing accuracy and speed',
                'low': 'efficient reasoning, optimising for speed and cost'}
_QWEN = dict(temperature=1.0, top_p=.95, top_k=20, min_p=0., presence_penalty=0., repeat_penalty=1.)
_QWEN_INSTRUCT = dict(_QWEN, temperature=.7, top_p=.8, presence_penalty=1.5,
                      when='chat, quick answers, simple edits, many small subagents — faster')
_QWEN38 = {'thinking': dict(_QWEN, max_output={'reasoning': 262144, 'answer': 131072},
                            when='math, coding, multi-step planning, agent work — slower, longer'),
           'instruct': _QWEN_INSTRUCT, 'effort': _QWEN_EFFORT}
_MIMO = {'thinking': dict(temperature=1.0, top_p=.95, when='agentic coding, planning, hard reasoning'),
         'instruct': dict(temperature=1.0, top_p=.95, when='quick replies, routine edits; same sampling (the card gives one set)'),
         'source_url': 'https://huggingface.co/XiaomiMiMo/MiMo-V2.6-Flash-RL'}
RECOMMENDED = {
    'qwen35': dict(_QWEN38, source_url='https://huggingface.co/Qwen/Qwen3.8-27B'),
    'qwen4exp': dict(_QWEN38, source_url='https://huggingface.co/Qwen/Qwen3.8-Flash-Next'),
    'qwen35moe': {'thinking': dict(_QWEN, temperature=.6, max_output=16384,
                                   when='math, code, multi-step reasoning (trained on hard math and contest code)'),
                  'instruct': dict(_QWEN_INSTRUCT, when='quick chat, simple edits; the publisher did not evaluate this mode'),
                  'source_url': 'https://huggingface.co/empero-ai/Qwen3.8-35B-A3B-Distill'},
    'mimo_v2': _MIMO, 'mimo2': _MIMO,
    'glm5next': {'thinking': dict(temperature=1.0, top_p=.95, when='every reply thinks; choose the effort'),
                 'instruct': None, 'note': 'Thinking off is unofficial: the model card has no non-thinking mode.',
                 'effort': {'max': 'benchmarks, hard coding and agent work (card default)',
                            'high': 'faster, cheaper turns', 'low': 'fastest, cheapest turns'},
                 'source_url': 'https://huggingface.co/zai-org/GLM-5.3-Flash'},
    'deepseek_v41': {'thinking': dict(temperature=1.0, top_p=.95, max_output=262144, when='coding, agent work, hard problems'),
                     'instruct': dict(temperature=1.0, top_p=.95, label='Chat',
                                      when='quick answers; same sampling (the card gives one set)'),
                     'effort': {'max': 'agent and coding work (the card\'s benchmark setting)', 'high': 'the default',
                                'low': 'faster turns, less thorough'},
                     'source_url': 'https://huggingface.co/deepseek-ai/DeepSeek-V4.1-Flash'},
}


def _number(key, value):
    return f'{value:.1f}' if key == 'temperature' and value == round(value, 1) else f'{value:g}'


def _tokens(value):
    if isinstance(value, dict):
        return f"{_tokens(value.get('reasoning'))} reasoning / {_tokens(value.get('answer'))} answer"
    return (f'{value // 1024}K' if value % 1024 == 0 else f'{value:,}') if type(value) is int else str(value)


def carded_model(architecture, meta):
    """The built-in row for these exact weights, or None. Mirrors MachX's recommended_sampling.hpp (Qwen3.8 = the
    template's xhigh sentence) and is stricter where the header allows: the distill (header name 'Ours', base
    Qwen3.6-35B-A3B; its template equals the base's), GLM-5.3-Flash and the MiMo GGUF by name. The directory models
    (deepseek_v41, mimo_v2) match by architecture, as in the engine. Other files of these architectures get nothing."""
    name = re.sub(r'[ _-]+', '-', str(meta.get('general.name', '')).lower())
    template = str(meta.get('tokenizer.chat_template', ''))
    if architecture in ('qwen35', 'qwen4exp'):
        return architecture if 'Reasoning effort is set to xhigh.' in template else None
    if architecture == 'qwen35moe':
        distill = 'qwen3.8-35b-a3b-distill' in name or (
            name == 'ours' and meta.get('general.base_model.0.repo_url') == 'https://huggingface.co/Qwen/Qwen3.6-35B-A3B')
        return architecture if distill and '<think>' in template else None
    if architecture in ('deepseek_v41', 'mimo_v2'):
        return architecture
    wanted = {'glm5next': 'glm-5.3-flash', 'mimo2': 'mimo-v2.6-flash'}.get(architecture)
    return architecture if wanted and wanted in name else None


def recommended_modes(capabilities, architecture, thinking, meta):
    """The recommendation block for this model: the engine's own when it sends the key (`null` = none), else the
    built-in copy for exactly carded weights; None otherwise. Each mode gets `values` (applied: card values + NEUTRAL
    for the rest) and a one-line `line` for the load screen."""
    if 'recommended' in capabilities:
        block = capabilities['recommended']
        block = block if isinstance(block, dict) and isinstance(block.get('thinking'), dict) else None
    else:
        block = RECOMMENDED.get(carded_model(architecture, meta))
    if not block:
        return None
    modes = {'source': str(block.get('source_url', '')), 'note': str(block.get('note', '')),
             'effort': block['effort'] if isinstance(block.get('effort'), dict) else {}}
    for name in ('thinking', 'instruct'):
        raw = block.get(name)
        if not isinstance(raw, dict):
            modes[name] = None
            continue
        card = {ALIASES.get(k, k): v for k, v in raw.items() if type(v) in (int, float)
                and ALIASES.get(k, k) in ('temperature', 'top_p', *NEUTRAL)}
        shown = [f"{label} {_number(key, card[key])}" for key, label in (('temperature', 'temp'), ('top_p', 'top_p'),
                 ('top_k', 'top_k'), ('presence_penalty', 'presence')) if key in card]
        output = raw.get('max_output', raw.get('max_tokens', block.get('max_output') if name == 'thinking' else None))
        if output:
            shown.append('max output ' + _tokens(output))
        title = str(raw.get('label') or name.title())
        modes[name] = {'values': {**NEUTRAL, **card}, 'when': str(raw.get('when', '')), 'label': title,
                       'line': title + ' — ' + ' · '.join(shown)}
    modes['active'] = 'instruct' if thinking is False and modes['instruct'] else 'thinking'
    return modes


def read_metadata(path):
    """Read scalar model facts; skip tokenizer arrays without reading weight tensors."""
    if Path(path).is_dir():
        from .models import read_checkpoint_json
        config = read_checkpoint_json(Path(path) / 'config.json')
        architecture = config.get('model_type')
        if not isinstance(architecture, str):
            raise ValueError('Checkpoint architecture is missing')
        text = config.get('text_config', config)
        if not isinstance(text, dict):
            raise ValueError('Invalid checkpoint text config')
        result = {'general.name': Path(path).name, 'general.architecture': architecture}
        for source, target in [('max_position_embeddings', 'context_length'),
                               ('num_hidden_layers', 'block_count'),
                               ('hidden_size', 'embedding_length')]:
            if type(text.get(source)) is int:
                result[f'{architecture}.{target}'] = text[source]
        return result
    formats={0:'B',1:'b',2:'H',3:'h',4:'I',5:'i',6:'f',7:'?',10:'Q',11:'q',12:'d'}
    with Path(path).open('rb') as f:
        def take(n):
            if n<0 or f.tell()+n>HEADER_LIMIT:raise ValueError('GGUF metadata exceeds read budget')
            b=f.read(n)
            if len(b)!=n:raise ValueError('Truncated GGUF metadata')
            return b
        def number(fmt):return struct.unpack('<'+fmt,take(struct.calcsize('<'+fmt)))[0]
        def string(keep=True):
            n=number('Q')
            if n>HEADER_LIMIT or f.tell()+n>HEADER_LIMIT:raise ValueError('GGUF string exceeds read budget')
            if keep:return take(n).decode('utf8',errors='replace')
            f.seek(n,1);return None
        def value(kind,keep,depth=0):
            if depth>2:raise ValueError('Nested GGUF metadata exceeds depth budget')
            if kind in formats:
                v=number(formats[kind]);return v if keep else None
            if kind==8:return string(keep)
            if kind==9:
                subtype,count=number('I'),number('Q')
                if count>1_000_000:raise ValueError('GGUF array exceeds count budget')
                if subtype in formats:
                    n=count*struct.calcsize('<'+formats[subtype])
                    if f.tell()+n>HEADER_LIMIT:raise ValueError('GGUF array exceeds read budget')
                    f.seek(n,1)
                else:
                    for _ in range(count):value(subtype,False,depth+1)
                return None
            raise ValueError('Unsupported GGUF metadata type')
        if take(4)!=b'GGUF':raise ValueError('Missing GGUF header')
        if number('I') not in (2,3):raise ValueError('Unsupported GGUF version')
        number('Q');count=number('Q')
        if count>100000:raise ValueError('GGUF metadata count exceeds budget')
        result={}
        for _ in range(count):
            key=string();kind=number('I')
            keep=key in {'general.name','general.architecture','tokenizer.chat_template','general.base_model.0.repo_url'} or key.endswith(('.context_length','.block_count','.embedding_length','.attention.head_count','.attention.head_count_kv','.attention.key_length','.attention.value_length','.expert_count'))
            v=value(kind,keep)
            if keep and v is not None:result[key]=v
        if f.tell()>Path(path).stat().st_size:raise ValueError('Truncated GGUF metadata')
        return result


def inspect_hardware():
    """Read host capacity, without starting telemetry workers or allocating GPU memory."""
    result={'devices':[]}
    try:
        from ..telemetry import GpuSampler
        result.update(asdict(GpuSampler().sample_once()))
    except Exception as exc:result['reason']=f'GPU capacity unavailable: {type(exc).__name__}'
    try:
        mem={line.split(':')[0]:int(line.split()[1])*1024 for line in Path('/proc/meminfo').read_text().splitlines() if line.startswith(('MemTotal:','MemAvailable:'))}
        result['ram_total_gb']=mem['MemTotal']/2**30
        result['ram_available_gb']=mem['MemAvailable']/2**30
    except (OSError,ValueError,KeyError):pass
    return result


def recommend(path,capabilities,*,hardware=None):
    controls=available_controls(capabilities)
    options={c.name:c.default for c in controls};sources={c.name:c.source for c in controls}
    notes=[]
    try:meta=read_metadata(path)
    except (OSError,ValueError,struct.error) as exc:
        meta={};notes.append(f'Model metadata unavailable: {exc}; using conservative fallback.')
    architecture=meta.get('general.architecture',capabilities.get('architecture',''))
    if architecture == 'glm5next':
        policy = capabilities.get('memory_policy', {})
        if not isinstance(policy, dict):
            policy = {}
        if policy.get('host_banks') in ('pinned_auto', 'mmap'):
            host = 'pinning enabled within the RAM safety budget' if policy['host_banks'] == 'pinned_auto' and policy.get('pin_max_bytes') != 0 else 'pinning disabled by engine override'
            cache = 'automatic GPU expert-cache budget' if policy.get('expert_cache_bytes') == 0 else 'explicit GPU expert-cache budget'
            notes.append(f'GLM loading: host-bank {host}; {cache}. Actual residency is reported after loading.')
        else:
            notes.append('Rebuild MachX to enable resource-aware GLM loading; this backend does not report the new memory policy.')
    modes=recommended_modes(capabilities,architecture,options.get('thinking'),meta)
    if modes:
        source='Model card · '+modes['source'].removeprefix('https://')
        for key,val in modes[modes['active']]['values'].items():
            if key in options:options[key]=val;sources[key]=source
        if modes['instruct'] is None and 'thinking' in options:
            sources['thinking']='Model card: always thinks; off is unofficial'
    hardware=inspect_hardware() if hardware is None else hardware
    devices=[d for d in hardware.get('devices',[]) if not d.get('integrated')]
    maximum=capabilities.get('max_gpus')
    gpus=min(len(devices),maximum or len(devices)) or None
    limit=meta.get(str(architecture)+'.context_length')
    if type(limit) is not int or limit<9:limit=None
    # Training length is an upper bound, never a memory-fit guarantee. Start with
    # a useful conservative budget; shrink further on small GPUs/low host RAM.
    ctx=32768
    smallest=min((d.get('vram_total_mib') or 0 for d in devices[:gpus]),default=0)
    if smallest and smallest<8192:ctx=8192
    elif smallest and smallest<16384:ctx=16384
    if hardware.get('ram_total_gb',64)<16:ctx=min(ctx,8192)
    if limit:ctx=min(ctx,limit)
    if 'max_tokens' in options:
        bounded=min(options['max_tokens'],max(1,ctx//2))
        if bounded!=options['max_tokens']:sources['max_tokens']+='; bounded to half the selected context'
        options['max_tokens']=bounded
    sources.update(gpus='Detected discrete GPUs + engine limit' if devices else 'Engine auto; hardware detection unavailable',ctx='Dream conservative starting context'+('; capped by model training limit' if limit else ''))
    notes.append('Context is a starting recommendation, not a calculated memory-fit maximum. Advanced can increase it; live preflight and the engine still validate allocation.')
    return {'gpus':gpus,'ctx':ctx,'options':options,'sources':sources,'context_limit':limit,'notes':notes,'metadata':meta,'modes':modes}
