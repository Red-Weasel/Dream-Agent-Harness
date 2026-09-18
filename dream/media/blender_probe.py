"""Fixed diagnostic script executed only inside an explicitly approved Blender."""
import json

MARKER = 'DREAM_BLENDER_CAPABILITIES_V1:'
ENGINE_NAMES = ('CYCLES', 'BLENDER_EEVEE_NEXT', 'BLENDER_EEVEE')


def collect():
    import bpy
    import addon_utils

    options = bpy.app.build_options
    build = getattr(options, 'cycles', None)
    errors = []
    registered = 'cycles' in bpy.context.preferences.addons
    registration = 'available' if registered else 'failed'
    if not registered:
        def handle_error(*_args):
            import traceback
            errors.append(traceback.format_exc()[-1000:])
        try:
            module = addon_utils.enable('cycles', default_set=False, persistent=False,
                                        handle_error=handle_error)
            if module is not None or 'cycles' in bpy.context.preferences.addons:
                registration = 'enabled'
            elif not errors:
                errors.append('Cycles registration returned no module')
        except Exception as exc:
            errors.append(f'{type(exc).__name__}: {exc}'[:1000])
    engines = {}
    render = bpy.context.scene.render
    original = render.engine
    try:
        for name in ENGINE_NAMES:
            try:
                render.engine = name
                selectable = render.engine == name
                engines[name] = {'selectable': selectable,
                                 'error': None if selectable else 'Engine assignment was not retained'}
            except Exception as exc:
                engines[name] = {'selectable': False, 'error': f'{type(exc).__name__}: {exc}'[:1000]}
    finally:
        render.engine = original
    return {
        'schema_version': 1,
        'version': bpy.app.version_string,
        'cycles': {'build_enabled': build if type(build) is bool else None,
                   'registration': registration, 'error': '\n'.join(errors)[:2000] or None},
        'engines': engines,
        'backend_build_options': {name: getattr(options, name) for name in
                                  ('cycles', 'cycles_osl', 'cycles_path_guiding', 'cuda', 'hip', 'metal', 'oneapi')
                                  if type(getattr(options, name, None)) is bool},
        'devices': 'unverified', 'headless_render': 'unverified',
    }


if __name__ == '__main__':
    print(MARKER + json.dumps(collect()), flush=True)
