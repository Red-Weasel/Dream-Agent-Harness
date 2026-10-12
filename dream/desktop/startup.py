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

    from ..local.models import _base_roots, model_details

    rows, auxiliary, unavailable = [], [], []

    def shown(label, sees):
        return label + (' · sees images' if sees else ' · text only' if sees is False else ' · vision unreported')

    model = machx.served_model_id()
    if model:
        sees = None  # File/engine capabilities are inspected only after selection.
        rows.append(dict(id='running', label=shown(f'{model} · already running', sees), provider='machx',
                         model=model, ready=True, kind='attach', vision=sees,
                         display_name=model, load_method='Connect to running model',
                         note='Uses the loaded model. Leaves it running on exit.'))
    for row in detect_providers():
        if row.key == 'moe':
            continue  # Advisors are optional alongside the selected main engine.
        rows.append(dict(id=row.key, label=row.label, provider=row.key, model=None,
                         display_name=row.label, load_method='Use installed client',
                         ready=row.ready, kind='provider', note=(
                             'Installed client detected. Account access is checked on your first message.'
                             if row.ready else 'Set up and sign in to this provider client, then refresh.')))
    if machx.available():
        for item in machx.list_models_detailed():
            try:
                details = model_details(item.path, shard_count=_catalog_shard_count(item))
            except (OSError, ValueError):
                unavailable.append({'path': str(item.path), 'name': item.name,
                                    'note': 'Files changed or became unavailable during discovery. Refresh to retry.'})
                continue
            row = _local_choice(item, details)
            (auxiliary if details['auxiliary_kind'] else rows).append(row)
    saved = load_config()
    return {'choices': rows, 'auxiliary': auxiliary, 'unavailable': unavailable, 'council_choices': provider_choices(),
            'discovery': {'scope': 'Known model folders and mounted drives; bounded search, not the entire computer.',
                          'roots': list(dict.fromkeys(str(p) for p in _base_roots())),
                          'unavailable_count': len(unavailable),
                          'refresh': 'Refresh searches these locations again. Browse chooses a file or folder directly.',
                          'privacy': 'Models stay on this computer. Browsing does not upload or copy files.'},
            'saved_council': asdict(saved) if saved else None}


def _catalog_shard_count(item):
    import re
    if item.path.is_dir():
        from ..local.models import directory_model_files
        return sum(p.suffix == '.safetensors' for p in directory_model_files(item.path))
    match = re.search(r'\((\d+) parts\)$', item.name)
    return int(match.group(1)) if match else 1


def _local_choice(item, details, *, discovered_by='scan'):
    parts = [details['display_name'], details['quantization'], details['format'],
             f'{item.size_gb:.1f} GB', item.volume]
    return dict(details, id=str(item.path), path=str(item.path),
                label=' · '.join(p for p in parts if p), size_gb=item.size_gb,
                provider='machx', ready=True, kind='local', discovered_by=discovered_by,
                load_method='Load local model',
                note='Local files stay on this computer. Loading starts only when you choose Start.')


def inspect_choice(path):
    """Inspect an explicit local selection. Does not load, copy or persist it."""
    from ..local.models import inspect_model
    item, details = inspect_model(path)
    settings = model_settings(item.path)
    settings['model_details'] = details
    return {'choice': _local_choice(item, details, discovered_by='browse'), 'settings': settings}


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
    from ..local.models import DIRECTORY_ARCHITECTURES, LANE_ARCHITECTURES, model_details
    from ..local.model_defaults import model_facts
    if caps.get('architecture') in DIRECTORY_ARCHITECTURES and caps.get('memory_planner') == 'streaming':
        memory_notes = ['Memory: this model uses GPU slots, bounded pinned RAM and disk-backed weights. '
                        'The full checkpoint need not fit in RAM. The engine keeps 40 GiB of host RAM '
                        'available and sizes expert slots from free GPU memory. It uses all visible Arc GPUs '
                        + ('and serves as many requests at once as its parallel below '
                           + ('(2 or more need two cards).' if caps.get('architecture') == 'deepseek_v41'
                              else '(images need 1).')      # DREAM-154: V4.1 keeps its images
                           if caps.get('architecture') in LANE_ARCHITECTURES
                           else 'and serves one request at a time (parallel = 1).')]
    return dict(selection=selection, recommended={k: rec[k] for k in ('gpus', 'ctx', 'options')},
                sources=sources, recommended_sources=rec['sources'], notes=memory_notes + rec['notes'] + caps.get('notes', []),
                context_limit=rec['context_limit'], max_gpus=rec['gpus'] or caps.get('max_gpus'),
                controls=[asdict(c) for c in available_controls(caps)],
                identity=identity, revision=saved['revision'] if saved else None,
                architecture=caps.get('architecture'), memory_policy=caps.get('memory_policy', {}),
                memory_planner=caps.get('memory_planner'), capabilities=caps, modes=rec.get('modes'),
                model_details=model_details(path, metadata=rec.get('metadata')),
                facts=model_facts(rec, caps, selection, path=path))


def validate_selection(selection, settings):
    from ..local.settings import validate_options
    ctx, gpus = selection['ctx'], selection['gpus']
    if type(ctx) is not int or not 9 <= ctx <= min(settings.get('context_limit') or 2**31-1, 2**31-1):
        raise ValueError('Context must be within the model limit and at least 9.')
    maximum = settings.get('max_gpus') or 64
    if gpus is not None and (type(gpus) is not int or not 1 <= gpus <= maximum):
        raise ValueError('GPU count exceeds available hardware or engine limits.')
    controls = {c['name']: c for c in settings['controls']}
    if selection['options'].get('context_overflow') == 'error':     # stop's name before DREAM-175: old saves load
        selection = dict(selection, options=dict(selection['options'], context_overflow='stop'))
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
    from ..local.models import DIRECTORY_ARCHITECTURES
    disk_streaming = (settings.get('architecture') in DIRECTORY_ARCHITECTURES
                      and settings.get('memory_planner') == 'streaming')
    # ds41_load / mimo26_load enumerate all visible Arc devices; --gpus does not restrict them.
    if disk_streaming and count != len(devices):
        raise ValueError('This model uses all visible GPUs. Select auto or all detected GPUs so every card is checked.')
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
            raise ValueError('This model needs more than 40 GiB of available host RAM for its pinned tier and reserve.')
        if not disk_streaming and available_ram < model_gb * 1.08:
            raise ValueError('MachX streams experts from host RAM, but available RAM is below the weight size plus headroom. Free memory before loading.')
        if any((d.get('vram_total_mib', 0) - d.get('vram_used_mib', 0)) < 8192 for d in devices[:count]):
            raise ValueError('Not enough free GPU memory for base weights and caches.')
        if disk_streaming:
            return ('This model streams through GPU slots, bounded pinned RAM and disk-backed weights; '
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


STOP_WAIT_S = 10.0      # how long stop_owned waits for the child's orderly stop (0.3-1.1 s on the measured runs)


def stop_owned(proc):
    """Stop only the exact child launched by this bootstrap, never a PID-file target: SIGTERM, its orderly stop,
    once, then a wait of at most STOP_WAIT_S. Never SIGKILL (DREAM-253: a forced termination left bad GPU pages
    and an unkillable process). True when the child is gone; False when it is still running after the wait."""
    if proc.poll() is not None:
        return True
    try:
        proc.terminate()
    except ProcessLookupError:
        pass                    # it exited between the poll and the signal; the wait below reaps it
    try:
        proc.wait(timeout=STOP_WAIT_S)
    except subprocess.TimeoutExpired:
        return False
    return True


def _still_running(proc):
    return (f"The engine (pid {getattr(proc, 'pid', 'unknown')}) is still running: it was asked to stop and had "
            f"not exited after {STOP_WAIT_S:g} s. Dream did not force-kill it, and its GPU memory is not free "
            "until it exits.")


# The sidebar's Unload model and Load model (2026-10-10): switch the local model while the session and its terminals
# stay open. A model loaded that way outlives the short `load` process (keep_hot); this file beside the status file
# names it, so the session that owns this window stops it on exit as it stops the one it loaded itself.
LOADED_MARKER = 'loaded-engine.json'


def _stop_loaded(status_path):
    """At the session's end: stop a model the sidebar loaded during it, if that engine is still the one serving."""
    from ..local import machx
    marker = Path(status_path).parent / LOADED_MARKER
    try:
        pid = int(json.loads(marker.read_text())['pid'])
    except (OSError, ValueError, KeyError, TypeError):
        return
    try:
        running = int(machx._pid_file().read_text().strip())
    except (OSError, ValueError):
        running = None
    if running == pid and machx._alive(pid) and not machx.stop():
        print(f'The model loaded during this session (pid {pid}) is still running: it was asked to stop and had not '
              'exited. Dream did not force-kill it.', flush=True)
    marker.unlink(missing_ok=True)


def unload():
    """Stop the local engine Dream started, politely (machx.stop: SIGTERM and a wait, never SIGKILL), and say what
    happened. An engine Dream did not start is left alone."""
    from ..local import machx
    model = machx.served_model_id()
    try:
        pid = int(machx._pid_file().read_text().strip())
    except (OSError, ValueError):
        pid = None
    if pid is None or not machx._alive(pid):
        if model:
            return dict(stopped=False, model=model, message=f'{model} was not started by Dream, so Dream leaves it '
                        'running. Use `ie stop` in a terminal to unload it.')
        return dict(stopped=False, model=None, message='No local model is loaded.')
    if machx.stop():
        return dict(stopped=True, model=model, message=f'Unloaded {model or "the local model"}. Its GPU memory is free. '
                    'Terminals stay open; use Load model to choose another.')
    return dict(stopped=False, model=model, message=f'{model or "The local model"} (pid {pid}) was asked to stop and is '
                'still running. Dream did not force-kill it; its GPU memory is not free until it exits.')


async def load(request, status_path):
    """Load a local model for the sessions already open, without starting a conversation. The engine outlives this
    process; the window's own session stops it at its end (LOADED_MARKER)."""
    choice = request.get('choice') or {}
    if choice.get('kind') != 'local' or choice.get('provider') != 'machx':
        raise ValueError('Choose a local model to load.')
    owned, failure = {}, None
    with ExitStack() as lifetime:
        lifetime.enter_context(local_load_lock())
        try:
            proc, model, _, _ = await _load_local(choice, request, status_path, owned, keep_hot=True)
            marker = Path(status_path).parent / LOADED_MARKER
            marker.write_text(json.dumps(dict(pid=proc.pid, model=model)))
            marker.chmod(0o600)
        except BaseException as exc:
            failure = exc
            raise
        finally:
            if failure is not None and owned.get('proc') is not None:
                await _stop_child(owned['proc'], status_path, failure)
    publish(status_path, 'ready', f'{model} is loaded. Open terminals switch to it at their next message.')
    return model


# What run()'s cleanup found about its engine while a cancellation or interrupt was on its way out. Kept here, not
# only as a note on that exception: asyncio.run() answers a SIGINT-cancelled task with a new KeyboardInterrupt,
# which carries no notes. main() clears it before a run and publishes it in the cancelled status.
_cleanup_warnings = []


def _warn(failure, text):
    """Attach what the cleanup found to the failure on its way out, where main() can publish it."""
    if failure is not None:
        failure.add_note(text)
    if not isinstance(failure, Exception):      # a cancellation or interrupt (or one that arrived mid-cleanup)
        _cleanup_warnings.append(text)
        print(text, flush=True)


async def _stop_child(proc, status_path, failure):
    """run()'s cleanup of its own child. An engine that did not stop is said plainly: as a note on the failure
    already on its way out (`failure`; main() publishes it with its notes), in `_cleanup_warnings` when that
    failure is a cancellation or interrupt (main() publishes those too), or as the session's last status.
    The cleanup never replaces an error with one of its own; only a new cancellation or interrupt that arrives
    during the stop is passed on, with what is known of the engine recorded first."""
    try:
        stopped = await asyncio.to_thread(stop_owned, proc)
    except Exception as exc:
        if failure is None:
            raise
        _warn(failure, f"Stopping the engine (pid {getattr(proc, 'pid', 'unknown')}) also failed: {exc!r}. "
                       "It may still be running.")
        return
    except BaseException:       # cancelled or interrupted during the stop: whether the engine exited is not known
        _warn(None, f"Dream was interrupted while stopping the engine (pid {getattr(proc, 'pid', 'unknown')}) "
                    "and did not confirm that it exited. It may still be running; Dream did not force-kill it.")
        raise
    if stopped:
        return
    if failure is None:
        publish(status_path, 'ended', 'Session saved. ' + _still_running(proc))
        return
    _warn(failure, _still_running(proc))


async def _load_local(choice, request, status_path, owned, *, keep_hot=False):
    """Check, preflight and load one local model; the caller holds the launch lock. The engine is put in
    `owned['proc']` as soon as it is started, so a caller's cleanup can stop it when loading fails.
    Returns (proc, served model id, engine options, capabilities)."""
    from ..local import machx
    from ..local.model_presets import Presets, model_key
    path = Path(choice['path']).resolve(strict=True)
    browsed = None
    if choice.get('discovered_by') == 'browse':
        from ..local.models import inspect_model
        browsed, _ = await asyncio.to_thread(inspect_model, choice.get('selected_path') or path)
        if browsed.path != path or choice.get('id') != str(path):
            raise ValueError('Selected model path changed. Browse and select the model again.')
    publish(status_path, 'starting', 'Checking model settings and available GPU memory…')
    settings = await asyncio.to_thread(model_settings, path)
    if settings['identity'] != request['identity']:
        raise ValueError('Model files changed. Refresh and select the model again.')
    selection = validate_selection(request['selection'], settings)
    if browsed is not None:
        item = browsed
    else:
        models = await asyncio.to_thread(machx.list_models_detailed)
        item = next((m for m in models if m.path.resolve() == path), None)
    if item is None:
        raise ValueError('Model is no longer in the local catalog. Refresh and select again.')
    preflight = await asyncio.to_thread(launch_preflight, item.size_gb * 1e9 / 2**30, selection['gpus'], settings)
    publish(status_path, 'starting', preflight)
    if browsed is not None and Path(choice.get('selected_path') or path).resolve(strict=True) != path:
        raise ValueError('Selected model link changed during preflight. Browse and select it again.')
    if machx.is_serving() or model_key(path) != settings['identity']:
        raise ValueError('Server or model state changed during preflight. Refresh before trying again.')
    # DREAM-151: the model's own lanes (its parallel and sub-agent context, the load screen's) for a model that
    # serves lanes; the preset saves the selection as chosen.
    from ..local.settings import engine_lanes, lanes_report
    options, lanes, lanes_note = engine_lanes(selection['options'], settings.get('architecture'),
                                              ctx=selection['ctx'], gpus=selection['gpus'])
    if lanes_note:
        publish(status_path, 'starting', lanes_note)
    session_capabilities = settings.get('capabilities')
    publish(status_path, 'loading', f"Loading {item.name}. Large models can take several minutes. Full loading log: {machx._log_file()}.")
    mark = machx.log_mark()
    proc = owned['proc'] = machx.serve(path, gpus=selection['gpus'], ctx=selection['ctx'], options=options,
                                       **({'keep_hot': True} if keep_hot else {}))
    if not await asyncio.to_thread(machx.wait_ready, proc):
        said = machx.launch_error(mark)
        raise ValueError(f'Model did not become ready. Review {machx._log_file()}, then retry.'
                         + (f' The engine said: {said}' if said else ''))
    model = machx.served_model_id() or item.name
    if lanes:
        publish(status_path, 'starting', lanes_report(lanes, await asyncio.to_thread(machx.served_lanes),
                                                      settings.get('architecture')))
    try:
        Presets().save(path, selection, expected=request.get('revision'), expected_key=settings['identity'])
    except (ValueError, OSError) as exc:
        publish(status_path, 'starting', f'Model loaded; settings were not saved: {exc}')
        print(f'Model settings not saved: {exc}', flush=True)
    return proc, model, options, session_capabilities


async def run(request, status_path):
    from .. import config
    from ..local import machx
    from ..local.settings import session_options, _validated_session_capabilities

    workspace = Path(request['workspace']).expanduser().resolve(strict=True)
    if not workspace.is_dir():
        raise ValueError('Choose an existing workspace folder.')
    choice = request['choice']
    provider, model = choice['provider'], choice.get('model')
    if provider not in ('anthropic', 'codex', 'grok', 'gemini', 'antigravity', 'machx'):
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
    if choice['kind'] not in ('provider', 'attach', 'local'):
        raise ValueError('Invalid launch choice.')
    # DREAM-259: accepted workspace selection initializes instructions before any
    # model connection/load. Failures remain warnings and do not reject a read-only project.
    from ..core.project_bootstrap import initialize
    for warning in await asyncio.to_thread(initialize, workspace):
        publish(status_path, 'starting', warning)
        print(warning, flush=True)
    proc = None
    owned = {}
    options = None
    session_capabilities = None
    load_lifetime = ExitStack()
    failure = None
    try:
        if choice['kind'] == 'attach':
            publish(status_path, 'starting', 'Connecting to the model already running…')
            current = await asyncio.to_thread(machx.served_model_id)
            if not current or current != model:
                raise ValueError('The running model changed or stopped. Refresh and select it again.')
            # The running model's capabilities, so the session knows what it can do -- image input first (DREAM-093).
            # Unknown weights attach exactly as before, with nothing reported; so does a report Dream cannot validate
            # (checked here, not first at session_options below, which would abort the attach -- DREAM-096).
            served = await asyncio.to_thread(machx.served_model_path)
            if served is not None:
                try:
                    session_capabilities = _validated_session_capabilities(
                        await asyncio.to_thread(machx.capabilities, served))
                    options = {}
                except (ValueError, OSError, subprocess.SubprocessError) as exc:
                    session_capabilities = None
                    publish(status_path, 'starting', f'Connected; the model\'s capabilities were not read: {exc}')
        elif choice['kind'] == 'local':
            load_lifetime.enter_context(local_load_lock())
            proc, model, options, session_capabilities = await _load_local(choice, request, status_path, owned)
            # The engine inherited the launch lease (machx._launch passes it), so it stays held while the engine
            # runs; this process's own copy goes now, so the sidebar's Load model can start another engine in this
            # session once this one is unloaded (2026-10-10).
            load_lifetime.close()
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
    except BaseException as exc:
        failure = exc           # the cleanup below reports on it and never replaces it
        raise
    finally:
        try:
            proc = proc or owned.get('proc')
            if proc is not None:
                await _stop_child(proc, status_path, failure)
            await asyncio.to_thread(_stop_loaded, status_path)
        finally:
            load_lifetime.close()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('action', choices=('catalog', 'settings', 'inspect', 'run', 'load', 'unload'))
    parser.add_argument('path', nargs='?')
    parser.add_argument('status', nargs='?')
    args = parser.parse_args()
    try:
        if args.action == 'catalog':
            print(json.dumps(catalog()))
        elif args.action == 'settings':
            print(json.dumps(model_settings(args.path)))
        elif args.action == 'inspect':
            print(json.dumps(inspect_choice(args.path)))
        elif args.action == 'unload':
            print(json.dumps(unload()))
        elif args.action == 'load':
            asyncio.run(load(json.loads(Path(args.path).read_text()), args.status))
        else:
            request = json.loads(Path(args.path).read_text())
            _cleanup_warnings.clear()
            asyncio.run(run(request, args.status))
    except KeyboardInterrupt as exc:
        if args.action in ('run', 'load') and args.status:
            # DREAM-253: an engine the cleanup could not confirm stopped stays in the last status the window
            # shows. From the cleanup's own record and from the interrupt's notes (the same text when the
            # interrupt was raised inside run() itself), each said once.
            warnings = list(dict.fromkeys([*_cleanup_warnings, *getattr(exc, '__notes__', ())]))
            publish(args.status, 'ended', ' '.join(['Startup cancelled.', *warnings]) if warnings
                    else 'Startup cancelled. You can choose an agent and try again.')
        raise SystemExit(130)
    except Exception as exc:
        # Notes: what run()'s cleanup found, e.g. an engine still running after its stop (DREAM-253).
        message = ' '.join([str(exc) or type(exc).__name__, *getattr(exc, '__notes__', ())])
        if args.action in ('run', 'load') and args.status:
            publish(args.status, 'error', message)
        else:
            print(json.dumps({'error': message}))
        raise SystemExit(1)


if __name__ == '__main__':
    from .crash_log import enable
    stop_crash_log = enable()   # DREAM-086: only the window's session launch sets the file
    try:
        main()
    finally:
        stop_crash_log()
