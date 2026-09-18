"""Graphical launch bridge, run in Dream's Python rather than GTK's Python.

Catalog/settings inspect metadata only. Only the explicit ``run`` action can
start a model; it uses live preflight and never stops an attached server.
"""
from __future__ import annotations

import argparse
import asyncio
from contextlib import ExitStack, contextmanager, nullcontext
from dataclasses import asdict
import json
import math
import os
import stat
from pathlib import Path
import subprocess
import time


def catalog():
    from ..core.council_config import provider_choices
    from ..core.moe import load_config
    from ..local import machx
    from ..tui.picker import detect_providers

    rows = []
    model = machx.served_model_id()
    if model:
        rows.append(dict(id='running', label=f'{model} · already running', provider='machx',
                         model=model, ready=True, kind='attach', note='Uses the loaded model. Leaves it running on exit.'))
    for row in detect_providers():
        if row.key == 'moe':
            continue  # Advisors are optional alongside the selected main engine.
        rows.append(dict(id=row.key, label=row.label, provider=row.key, model=None,
                         ready=row.ready, kind='provider', note=(
                             'Installed client detected. Account access is checked on your first message.'
                             if row.ready else 'Set up and sign in to this provider client, then refresh.')))
    if machx.available():
        for item in machx.list_models_detailed():
            filename = item.path.name.lower()
            if filename.startswith('mmproj') or filename == 'mtp-head.gguf':
                continue  # Auxiliary projection/MTP weights cannot serve chat alone.
            rows.append(dict(id=str(item.path), label=f'{item.name} · {item.size_gb:.1f} GB · {item.volume}',
                             path=str(item.path), provider='machx', ready=True, kind='local',
                             note='Load local weights using saved settings or recommendations.'))
    saved = load_config()
    return {'choices': rows, 'council_choices': provider_choices(),
            'saved_council': asdict(saved) if saved else None}


def model_settings(path):
    from ..local import machx
    from ..local.model_defaults import recommend
    from ..local.model_presets import Presets, model_key
    from ..local.settings import available_controls

    path = Path(path).expanduser().resolve(strict=True)
    identity = model_key(path)
    caps = machx.capabilities(path)
    if not caps.get('supported'):
        raise ValueError(f"MachX does not support {caps.get('architecture', 'this architecture')}.")
    rec = recommend(path, caps)
    saved = Presets().load(path)
    selection = {k: rec[k] for k in ('gpus', 'ctx', 'options')}
    sources = dict(rec['sources'])
    if saved:
        selection = {**saved['selection'], 'options': {**rec['options'], **saved['selection']['options']}}
        sources.update({key: 'Saved for this model' for key in ('gpus', 'ctx', *saved['selection']['options'])})
    memory_notes = (['Memory: MachX streams experts from host RAM and splits GPU work across the selected cards. '
                     'The full model file does not need to fit in VRAM; the engine checks weights, context and caches during startup.']
                    if caps.get('memory_planner') == 'streaming' else [])
    if caps.get('architecture') == 'deepseek_v41' and caps.get('memory_planner') == 'streaming':
        memory_notes = ['Memory: V4.1 uses GPU slots, bounded pinned RAM and disk-backed weights. '
                        'The full checkpoint need not fit in RAM. The engine keeps 40 GiB of host RAM '
                        'available and sizes expert slots from free GPU memory. It uses all visible Arc GPUs '
                        'and serves one request at a time (parallel = 1).']
    return dict(selection=selection, recommended={k: rec[k] for k in ('gpus', 'ctx', 'options')},
                sources=sources, recommended_sources=rec['sources'], notes=memory_notes + rec['notes'] + caps.get('notes', []),
                context_limit=rec['context_limit'], max_gpus=rec['gpus'] or caps.get('max_gpus'),
                controls=[asdict(c) for c in available_controls(caps)],
                identity=identity, revision=saved['revision'] if saved else None,
                architecture=caps.get('architecture'), memory_policy=caps.get('memory_policy', {}),
                memory_planner=caps.get('memory_planner'), capabilities=caps)


def validate_selection(selection, settings):
    from ..local.settings import validate_options
    ctx, gpus = selection['ctx'], selection['gpus']
    if type(ctx) is not int or not 9 <= ctx <= min(settings.get('context_limit') or 2**31-1, 2**31-1):
        raise ValueError('Context must be within the model limit and at least 9.')
    maximum = settings.get('max_gpus') or 64
    if gpus is not None and (type(gpus) is not int or not 1 <= gpus <= maximum):
        raise ValueError('GPU count exceeds available hardware or engine limits.')
    controls = {c['name']: c for c in settings['controls']}
    for name, value in selection['options'].items():
        if name not in controls or (controls[name]['choices'] and value not in controls[name]['choices']):
            raise ValueError(f'{name} is no longer supported. Refresh the model settings.')
    return dict(ctx=ctx, gpus=gpus, options=validate_options(
        selection['options'], ctx=ctx, gpus=gpus, architecture=settings.get('architecture')))


@contextmanager
def local_load_lock():
    """Hold the same inherited launch lease used by CLI and direct launches."""
    from ..local import machx
    from ..local.load_lock import load_lock
    with load_lock(machx.PORT):
        yield


def launch_preflight(model_gb, gpus, settings):
    """Check resources using the engine's reported residency strategy."""
    from ..local import machx
    from ..local.model_defaults import inspect_hardware
    from ..local.preflight import Verdict, check
    from ..telemetry import GpuSampler

    sampler = GpuSampler()
    sampler.sample_once()
    time.sleep(0.15)
    gpu = asdict(sampler.sample_once())
    devices = [d for d in gpu.get('devices', []) if not d.get('integrated')]
    count = gpus or len(devices)
    if not gpu.get('ok') or not devices or count > len(devices):
        raise ValueError('GPU status or requested topology is unavailable. No model was loaded.')
    disk_streaming = (settings.get('architecture') == 'deepseek_v41'
                      and settings.get('memory_planner') == 'streaming')
    # ds41_load enumerates all visible Arc devices; --gpus does not restrict it.
    if disk_streaming and count != len(devices):
        raise ValueError('V4.1 uses all visible GPUs. Select auto or all detected GPUs so every card is checked.')
    for device in devices[:count]:
        for key in ('util_pct', 'vram_total_mib', 'vram_used_mib'):
            value = device.get(key)
            if type(value) not in (int, float) or not math.isfinite(value) or value < 0:
                raise ValueError(f'GPU {key} is unavailable. No model was loaded; refresh after telemetry recovers.')
        if device['vram_total_mib'] <= 0 or device['vram_used_mib'] > device['vram_total_mib']:
            raise ValueError('GPU memory readings are inconsistent. No model was loaded.')
        if not isinstance(device.get('procs'), (list, tuple)):
            raise ValueError('GPU process visibility is unavailable. No model was loaded.')
        temperatures = [device.get(key) for key in ('temp_c', 'vram_temp_c')]
        if not any(type(value) in (int, float) and math.isfinite(value) for value in temperatures):
            raise ValueError('GPU temperatures are unavailable. No model was loaded.')
        for key in ('temp_c', 'vram_temp_c'):
            if device.get(key) is not None and device[key] >= 85:
                raise ValueError('A GPU is too hot to start another model. Let it cool, then refresh.')
        busy = [p for p in device.get('procs', [])
                if p['name'].lower() not in {'xorg', 'xwayland', 'gnome-shell', 'kwin_wayland', 'weston'}]
        if busy:
            names = ', '.join(f"{p['name']} (PID {p['pid']})" for p in busy)
            raise ValueError(f'GPU is in use by {names}. Connect to the running model or finish that work first.')
        if device['util_pct'] > 10:
            raise ValueError('GPU utilization is high. Finish the active workload before loading.')
    pf = check(model_gb=model_gb, gpus=count, serving=machx.is_serving(),
               served_id=machx.served_model_id(), gpu=gpu)
    policy = settings.get('memory_policy', {})
    # Capabilities are read again in run(), not accepted from the launch request.
    # Older engines reported GLM's explicit policy before adding memory_planner.
    streaming = (settings.get('memory_planner') == 'streaming' or (
                 settings.get('memory_planner') is None
                 and settings.get('architecture') == 'glm5next'
                 and policy.get('host_banks') in ('pinned_auto', 'mmap')
                 and policy.get('expert_cache_bytes') == 0))
    if (pf.verdict is Verdict.WONT_FIT and streaming) or (disk_streaming and pf.ok):
        host = inspect_hardware()
        available_ram = host.get('ram_available_gb')
        if type(available_ram) not in (int, float) or not math.isfinite(available_ram) or available_ram < 0:
            raise ValueError('Available host RAM is unknown. Refresh after memory telemetry recovers.')
        # Audited MachX Ds41Forward::init_resident caps pinning at MemAvailable
        # minus 40 GiB. Remaining experts/engram tables are mmap-backed, unlike
        # the GGUF streamers that require their weight banks to fit in host RAM.
        if disk_streaming and available_ram <= 40:
            raise ValueError('V4.1 needs more than 40 GiB of available host RAM for its pinned tier and reserve.')
        if not disk_streaming and available_ram < model_gb * 1.08:
            raise ValueError('MachX streams experts from host RAM, but available RAM is below the weight size plus headroom. Free memory before loading.')
        if any((d.get('vram_total_mib', 0) - d.get('vram_used_mib', 0)) < 8192 for d in devices[:count]):
            raise ValueError('Not enough free GPU memory for base weights and caches.')
        if disk_streaming:
            return ('V4.1 streams through GPU slots, bounded pinned RAM and disk-backed weights; '
                    f'host RAM available: {available_ram:.1f} GiB, with 40 GiB kept free by the engine. '
                    'The engine validates context, dense weights and remaining expert slots during startup.')
        return ('MachX streams experts from host RAM; the full weight size is not a VRAM requirement. '
                f'Host RAM available: {available_ram:.1f} GiB; GPU memory free: {pf.free_gb:.1f} GiB across {count} card(s). '
                'The engine validates the exact weights, context and cache allocation during startup.')
    if not pf.should_load:
        raise ValueError(pf.reason + ' Connect to the already running model or review local loading in Terminal.')
    return pf.reason


def publish(path, state, message):
    path = Path(path)
    temp = path.with_suffix('.tmp')
    temp.write_text(json.dumps(dict(state=state, message=message, updated_at=time.time())))
    print(message, flush=True)
    temp.chmod(0o600)
    temp.replace(path)


def stop_owned(proc):
    """Stop only the exact child launched by this bootstrap, never a PID-file target."""
    if proc.poll() is None:
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait(timeout=5)


async def run(request, status_path):
    from .. import config
    from ..local import machx
    from ..local.model_presets import Presets, model_key
    from ..local.settings import session_options

    workspace = Path(request['workspace']).expanduser().resolve(strict=True)
    if not workspace.is_dir():
        raise ValueError('Choose an existing workspace folder.')
    choice = request['choice']
    provider, model = choice['provider'], choice.get('model')
    if provider not in ('anthropic', 'codex', 'grok', 'gemini', 'machx'):
        raise ValueError('Choose a supported provider.')
    council = None
    if request.get('council') is not None:
        from ..core.council_config import parse_config
        council = parse_config(request['council'])
        if council.orchestrator != provider:
            raise ValueError('Council orchestrator must match the selected main agent.')
    if model is not None:
        from ..core.council_config import validate_model
        model = validate_model(model, allow_empty=True) or None
    proc = None
    options = None
    session_capabilities = None
    load_lifetime = ExitStack()
    try:
        if choice['kind'] == 'attach':
            publish(status_path, 'starting', 'Connecting to the model already running…')
            current = await asyncio.to_thread(machx.served_model_id)
            if not current or current != model:
                raise ValueError('The running model changed or stopped. Refresh and select it again.')
        elif choice['kind'] == 'local':
            load_lifetime.enter_context(local_load_lock())
            path = Path(choice['path']).resolve(strict=True)
            publish(status_path, 'starting', 'Checking model settings and available GPU memory…')
            settings = await asyncio.to_thread(model_settings, path)
            if settings['identity'] != request['identity']:
                raise ValueError('Model files changed. Refresh and select the model again.')
            selection = validate_selection(request['selection'], settings)
            models = await asyncio.to_thread(machx.list_models_detailed)
            item = next((m for m in models if m.path.resolve() == path), None)
            if item is None:
                raise ValueError('Model is no longer in the local catalog. Refresh and select again.')
            preflight = await asyncio.to_thread(launch_preflight, item.size_gb * 1e9 / 2**30, selection['gpus'], settings)
            publish(status_path, 'starting', preflight)
            if machx.is_serving() or model_key(path) != settings['identity']:
                raise ValueError('Server or model state changed during preflight. Refresh before trying again.')
            options = selection['options']
            session_capabilities = settings.get('capabilities')
            publish(status_path, 'loading', f"Loading {item.name}. Large models can take several minutes. Full loading log: {machx._log_file()}.")
            proc = machx.serve(path, gpus=selection['gpus'], ctx=selection['ctx'], options=options)
            if not await asyncio.to_thread(machx.wait_ready, proc):
                raise ValueError(f'Model did not become ready. Review {machx._log_file()}, then retry.')
            model = machx.served_model_id() or item.name
            try:
                Presets().save(path, selection, expected=request.get('revision'), expected_key=settings['identity'])
            except (ValueError, OSError) as exc:
                publish(status_path, 'starting', f'Model loaded; settings were not saved: {exc}')
                print(f'Model settings not saved: {exc}', flush=True)
        elif choice['kind'] != 'provider':
            raise ValueError('Invalid launch choice.')
        publish(status_path, 'starting', 'Opening your conversation…')
        from ..tui.app import App
        with session_options(options, model, capabilities=session_capabilities) if options is not None else nullcontext():
            app = App(provider=provider, model=model, workspace=workspace,
                      consolidate_on_exit=config.CONSOLIDATE_ON_EXIT, gui=True,
                      **({'moe': council} if council is not None else {}))
            await app.run()
        publish(status_path, 'ended', 'Session saved. Choose an engine to start another conversation.')
    finally:
        try:
            if proc is not None:
                await asyncio.to_thread(stop_owned, proc)
        finally:
            load_lifetime.close()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('action', choices=('catalog', 'settings', 'run'))
    parser.add_argument('path', nargs='?')
    parser.add_argument('status', nargs='?')
    args = parser.parse_args()
    try:
        if args.action == 'catalog':
            print(json.dumps(catalog()))
        elif args.action == 'settings':
            print(json.dumps(model_settings(args.path)))
        else:
            request = json.loads(Path(args.path).read_text())
            asyncio.run(run(request, args.status))
    except KeyboardInterrupt:
        if args.action == 'run' and args.status:
            publish(args.status, 'ended', 'Startup cancelled. You can choose an agent and try again.')
        raise SystemExit(130)
    except Exception as exc:
        message = str(exc) or type(exc).__name__
        if args.action == 'run' and args.status:
            publish(args.status, 'error', message)
        else:
            print(json.dumps({'error': message}))
        raise SystemExit(1)


if __name__ == '__main__':
    main()
