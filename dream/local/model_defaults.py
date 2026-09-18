"""Bounded model metadata and sourced local launch recommendations. No model loads."""
from dataclasses import asdict
from pathlib import Path
import struct
import re
from .settings import available_controls

CARD = 'https://huggingface.co/zai-org/GLM-5.3-Flash'
# Source reviewed 2026-09-06: coding evaluations use temp=1/top_p=1.
# This is a coding starting profile, not a claim of universally optimal sampling.
HEADER_LIMIT = 32 * 1024 * 1024


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
            keep=key in {'general.name','general.architecture'} or key.endswith(('.context_length','.block_count','.embedding_length','.attention.head_count','.attention.head_count_kv','.attention.key_length','.attention.value_length','.expert_count'))
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
    model_name=re.sub(r'[ _-]+','-',str(meta.get('general.name','')).lower())
    if architecture=='glm5next' and model_name == 'glm-5.3-flash':
        for key,val in {'temperature':1.0,'top_p':1.0}.items():
            if key in options:
                options[key]=val;sources[key]='Publisher coding profile (2026-09-06)'
        notes.append('GLM coding starting profile: '+CARD+'; evaluation settings, not a universal optimum. Top-k retains the backend limit.')
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
    return {'gpus':gpus,'ctx':ctx,'options':options,'sources':sources,'context_limit':limit,'notes':notes,'metadata':meta}
