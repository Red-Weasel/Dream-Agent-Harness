"""Small media tools sharing Studio and CLI operations."""
import json
from claude_agent_sdk import tool
from ..media.service import MediaService
from ..core.execution import ExecutionRefused
from .context import ctx, ok, err


async def _execute(args, allowed):
    action = args.get('action', '')
    if action not in allowed:
        return err('Unsupported media action')
    try:
        context = ctx()
        session_readiness = None
        if action == 'status':
            from ..core.execution import current_execution
            scope = current_execution(context.workspace).scope
            session_readiness = {
                'tool_images_enabled': context.multimodal,
                'image_acceptance_verified': None,
                'shell': {
                    'workspace': str(scope.workspace),
                    'read_roots': [str(path) for path in scope.read_roots],
                    'target_roots': [str(path) for path in scope.target_roots],
                    'availability_verified': None,
                    'evidence_source': 'Configured execution scope, not a sandbox launch or host-access grant.',
                },
                'next_step': 'Use the selected workspace. Host file reads and sandbox shell commands can '
                             'have different filesystem views. Check actual tool results before changing '
                             'paths or requesting additional access. Image configuration is not proof that '
                             'the loaded model accepts pixels; do not infer or load a vision sidecar.',
            }
        result = await MediaService(context.workspace).execute(action, args.get('payload', {}))
        if session_readiness is not None:
            result['session_readiness'] = session_readiness
        return ok(json.dumps(result, ensure_ascii=False))
    except (ValueError, KeyError, OSError, ExecutionRefused) as exc:
        return err(str(exc))
_READ = ['status', 'projects', 'get', 'jobs']
_WRITE = [
    'create',
    'save',
    'import',
    'render',
    'cancel',
    'handoff',
    'complete_handoff',
    'generate',
    'reconcile',
    'open_external',
    'recover',
    'probe_blender',
]


def _schema(actions):
    return {
        'type': 'object',
        'properties': {
            'action': {'type': 'string', 'enum': actions},
            'payload': {
                'type': 'object',
                'description': 'Media action payload. Project operations use project_id; job operations use job_id. Imports use workspace path.',
            },
        },
        'required': ['action'],
    }

@tool(
    'media_read',
    'Inspect workspace media status, projects, project assets or durable jobs. status includes passive Blender availability with unprobed engine/device evidence; it never launches Blender. get requires payload.project_id.',
    _schema(_READ),
)

async def media_read(args):
    return await _execute(args, _READ)

@tool(
    'media_create',
    'Create/edit/import/export workspace media. create: title; save: project_id, composition, expected_revision; render: project_id, format. Handoffs require project_id, provider, prompt; complete_handoff requires job_id and valid workspace output path. generate requires reviewed local workflow, resource_confirmed and workflow_confirmed; never infer those confirmations. probe_blender requires actual resource_confirmed and checks registration/engine selection in an isolated no-render Blender process; no project_id needed, no render/device qualification claimed. open_external opens explicit workspace .blend files. No API-key fallback.',
    _schema(_WRITE),
)

async def media_create(args):
    return await _execute(args, _WRITE)
MEDIA_TOOLS = [media_read, media_create]
