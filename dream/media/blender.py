"""Passive Blender availability and explicitly gated, bounded diagnostics."""
from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path
import shutil
import signal
import tempfile

from .blender_probe import ENGINE_NAMES, MARKER

PROBE_TIMEOUT = 30
OUTPUT_LIMIT = 128 * 1024
CLEANUP_GRACE = 0.25
# DREAM-141: the engine's oneAPI LD_LIBRARY_PATH makes a blender.org build's bundled SYCL load an
# older libur_loader, and Blender exits at start. Blender is run with its own libraries only.
LOADER_OVERRIDES = ('LD_LIBRARY_PATH', 'LD_PRELOAD')


def blender_environment() -> dict:
    """This process's environment without the dynamic-loader overrides, for launching Blender."""
    return {name: value for name, value in os.environ.items() if name not in LOADER_OVERRIDES}


def blender_status() -> dict:
    executable = shutil.which('blender')
    return {'available': executable is not None, 'support': 'external_editor',
            'executable': executable, 'probe_status': 'unprobed', 'version': None,
            'cycles': None, 'engines': None, 'devices': 'unverified',
            'headless_render': 'unverified'}


def _parse(output: bytes) -> dict:
    lines = output.decode('utf-8', errors='strict').splitlines()
    records = [line[len(MARKER):] for line in lines if line.startswith(MARKER)]
    if len(records) != 1:
        raise ValueError('Expected exactly one Blender diagnostic record')
    data = json.loads(records[0])
    if not isinstance(data, dict) or type(data.get('schema_version')) is not int or data['schema_version'] != 1:
        raise ValueError('Invalid Blender diagnostic schema')
    cycles = data.get('cycles')
    engines = data.get('engines')
    if (not isinstance(data.get('version'), str) or not data['version']
            or not isinstance(cycles, dict)
            or cycles.get('registration') not in ('available', 'enabled', 'failed')
            or 'build_enabled' not in cycles
            or (cycles['build_enabled'] is not None and type(cycles['build_enabled']) is not bool)
            or 'error' not in cycles or not (cycles['error'] is None or isinstance(cycles['error'], str))
            or not isinstance(engines, dict) or set(engines) != set(ENGINE_NAMES)):
        raise ValueError('Invalid Blender version, Cycles or engine evidence')
    for evidence in engines.values():
        if (not isinstance(evidence, dict) or type(evidence.get('selectable')) is not bool
                or 'error' not in evidence or not (evidence['error'] is None or isinstance(evidence['error'], str))):
            raise ValueError('Invalid Blender engine evidence')
    backends = data.get('backend_build_options')
    if (not isinstance(backends, dict) or any(type(v) is not bool for v in backends.values())
            or data.get('devices') != 'unverified' or data.get('headless_render') != 'unverified'):
        raise ValueError('Invalid Blender device evidence')
    # Never accept process-supplied availability, executable or probe status.
    return {key: data[key] for key in ('schema_version', 'version', 'cycles', 'engines',
                                      'backend_build_options', 'devices', 'headless_render')}


async def _read(process) -> bytes:
    output = bytearray()
    while True:
        chunk = await process.stdout.read(8192)
        if not chunk:
            break
        output.extend(chunk)
        if len(output) > OUTPUT_LIMIT:
            raise ValueError('Blender diagnostic exceeded output limit')
    await process.wait()
    if process.returncode:
        raise ValueError(f'Blender diagnostic exited with code {process.returncode}: '
                         + output[-2000:].decode(errors='replace'))
    return bytes(output)


async def _cleanup(process):
    # Do not signal a possibly reused process-group ID after the parent was reaped.
    if process.returncode is None:
        try:
            if os.name == 'posix':
                os.killpg(process.pid, signal.SIGKILL)
            else:
                process.kill()
        except ProcessLookupError:
            pass
    try:
        async with asyncio.timeout(CLEANUP_GRACE):
            while await process.stdout.read(8192):
                pass
    except TimeoutError:
        # An escaped descendant may retain stdout. Close our owned pipe, not that
        # descendant: factory-startup isolation is not process containment.
        process._transport.get_pipe_transport(1).close()
    await process.wait()


async def _finish(task):
    """Finish owned launch/cleanup even if cancellation is requested repeatedly."""
    cancelled = False
    while not task.done():
        try:
            await asyncio.shield(task)
        except asyncio.CancelledError:
            cancelled = True
    return task.result(), cancelled


async def probe_blender(*, resource_confirmed=False) -> dict:
    status = blender_status()
    if resource_confirmed is not True:
        return {**status, 'probe_status': 'confirmation_required',
                'error': 'Blender probing requires resource_confirmed=true after a resource preflight; native initialization may use GPU resources.'}
    if not status['available']:
        return {**status, 'probe_status': 'unavailable', 'error': 'Blender executable was not found on PATH'}
    with tempfile.TemporaryDirectory(prefix='dream-blender-probe-') as scratch:
        env = blender_environment()
        for name in tuple(env):
            if name.startswith(('BLENDER_USER_', 'BLENDER_SYSTEM_')) or name in ('PYTHONPATH', 'PYTHONHOME'):
                env.pop(name)
        env['BLENDER_USER_CONFIG'] = str(Path(scratch) / 'config')
        env['BLENDER_USER_SCRIPTS'] = str(Path(scratch) / 'scripts')
        env['BLENDER_USER_DATAFILES'] = str(Path(scratch) / 'datafiles')
        process = None
        launch = asyncio.create_task(asyncio.create_subprocess_exec(
            status['executable'], '--background', '--factory-startup', '--disable-autoexec',
            '--python-exit-code', '1', '--python', str(Path(__file__).with_name('blender_probe.py').resolve()),
            cwd=scratch, env=env, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT,
            start_new_session=os.name == 'posix'))
        try:
            async with asyncio.timeout(PROBE_TIMEOUT):
                process = await asyncio.shield(launch)
                output = await _read(process)
                evidence = _parse(output)
            return {**status, **evidence, 'probe_status': 'ok'}
        except TimeoutError:
            return {**status, 'probe_status': 'error', 'error': 'Blender diagnostic timed out'}
        except (OSError, ValueError) as exc:
            return {**status, 'probe_status': 'error', 'error': str(exc)[:2000]}
        finally:
            # Shield process creation so cancellation cannot orphan a just-created child.
            cancelled = False
            if process is None:
                try:
                    process, cancelled = await _finish(launch)
                except (OSError, ValueError):
                    pass
            if process is not None:
                _, cleanup_cancelled = await _finish(asyncio.create_task(_cleanup(process)))
                cancelled = cancelled or cleanup_cancelled
            if cancelled:
                raise asyncio.CancelledError()
