"""Explicit local workflows and honest subscription browser handoffs."""
import ipaddress
import json
import warnings
from pathlib import Path, PurePosixPath
from urllib.parse import urlsplit
import httpx
from .store import MediaError

# Match the file reader's per-image pixel ceiling. Animation validation also
# bounds total decoded work; exceeding a bound is incomplete, never a pass.
MAX_IMAGE_PIXELS = 25_000_000
MAX_IMAGE_TOTAL_PIXELS = 250_000_000
MAX_IMAGE_FRAMES = 1000
HANDOFFS = {
    'chatgpt': {'url': 'https://chatgpt.com/', 'support': 'browser_handoff'},
    'gemini': {'url': 'https://gemini.google.com/', 'support': 'browser_handoff'},
    'grok': {'url': 'https://grok.com/', 'support': 'browser_handoff'},
    'claude': {
        'url': 'https://claude.ai/',
        'support': 'browser_handoff',
        'note': 'Planning and coded visuals; photographic generation is not claimed.',
    },
    'codex': {
        'url': 'https://chatgpt.com/',
        'support': 'browser_handoff',
        'note': 'Child CLI media generation is unverified.',
    },
}
# Deliberately narrow built-in local nodes. Custom/partner node installation is
# outside this adapter's contract, even if its class name resembles a local node.
LOCAL_NODES = frozenset({
    'CheckpointLoaderSimple',
    'CLIPTextEncode',
    'EmptyLatentImage',
    'KSampler',
    'KSamplerAdvanced',
    'VAEDecode',
    'VAEEncode',
    'VAELoader',
    'SaveImage',
    'PreviewImage',
    'LoadImage',
    'ImageScale',
    'ImageScaleBy',
    'LoraLoader',
    'CLIPSetLastLayer',
    'ConditioningCombine',
    'ConditioningConcat',
    'SaveAnimatedWEBP',
    'SaveAnimatedPNG',
    'SaveVideo',
    'CreateVideo',
    'SaveWEBM',
    'ImageOnlyCheckpointLoader',
    'SVD_img2vid_Conditioning',
    'VideoLinearCFGGuidance',
})


def validate_workflow(workflow):
    if not isinstance(workflow, dict) or not workflow or len(workflow) > 200:
        raise MediaError('Provide an API workflow containing 1 to 200 local nodes')

    def inspect(value):
        if isinstance(value, str) and ('://' in value or value.startswith(('//', '\\\\'))):
            raise MediaError('Remote URLs are not allowed in local workflows')
        if isinstance(value, dict):
            for item in value.values():
                inspect(item)
        elif isinstance(value, list):
            for item in value:
                inspect(item)
    for node in workflow.values():
        if not isinstance(node, dict) or node.get('class_type') not in LOCAL_NODES or (not isinstance(node.get('inputs'), dict)):
            raise MediaError('Workflow contains an unapproved node class or invalid inputs')
        inspect(node)
        for key, value in node['inputs'].items():
            if key in {'image', 'video', 'ckpt_name', 'vae_name', 'lora_name', 'filename_prefix'} and isinstance(value, str):
                normalized = value.replace('\\', '/')
                if PurePosixPath(normalized).is_absolute() or '..' in PurePosixPath(normalized).parts or ':' in normalized:
                    raise MediaError('Workflow paths must stay relative to local backend folders')
    if len(json.dumps(workflow, allow_nan=False)) > 1000000:
        raise MediaError('Workflow exceeds size limit')
    return workflow


def _gif_frames(path):
    """Count complete GIF image blocks, never mistaking payload bytes for EOF.

    Pillow treats a missing trailing image/trailer as a shorter animation. Walk
    the bounded container first; pixel correctness still belongs to its decoder.
    """
    from .store import MediaStore

    with path.open('rb') as source:
        consumed = 0

        def read(size):
            nonlocal consumed
            consumed += size
            if consumed > MediaStore.MAX_ASSET_BYTES:
                raise MediaError('GIF validation incomplete: file exceeds the media byte limit.')
            data = source.read(size)
            if len(data) != size:
                raise MediaError('Incomplete GIF block or missing trailer. Re-export the animation.')
            return data

        def subblocks():
            while size := read(1)[0]:
                read(size)

        if read(6) not in (b'GIF87a', b'GIF89a'):
            raise MediaError('Invalid GIF format.')
        screen = read(7)
        width, height = int.from_bytes(screen[:2], 'little'), int.from_bytes(screen[2:4], 'little')
        if not width or not height or width * height > MAX_IMAGE_PIXELS:
            raise MediaError('GIF validation incomplete: canvas exceeds the image pixel limit.')
        if screen[4] & 0x80:
            read(3 * (1 << ((screen[4] & 7) + 1)))
        frames, total = 0, 0
        while True:
            kind = read(1)
            if kind == b';':
                if not frames or source.read(1):
                    raise MediaError('Invalid GIF trailer or trailing content. Re-export the animation.')
                return frames
            if kind == b'!':
                read(1)  # Extension label; its data consists of sized subblocks.
                subblocks()
            elif kind == b',':
                descriptor = read(9)
                left, top, frame_width, frame_height = (
                    int.from_bytes(descriptor[i:i + 2], 'little') for i in (0, 2, 4, 6))
                width, height = max(width, left + frame_width), max(height, top + frame_height)
                frames += 1
                total += width * height
                if (not frame_width or not frame_height or width * height > MAX_IMAGE_PIXELS
                        or total > MAX_IMAGE_TOTAL_PIXELS or frames > MAX_IMAGE_FRAMES):
                    raise MediaError('GIF validation incomplete: animation exceeds the image pixel or frame limit.')
                if descriptor[8] & 0x80:
                    read(3 * (1 << ((descriptor[8] & 7) + 1)))
                if not 2 <= read(1)[0] <= 8:
                    raise MediaError('Invalid GIF image code size.')
                subblocks()
            else:
                raise MediaError('Invalid GIF block. Re-export the animation.')


def _validate_image(path):
    from PIL import Image

    def pixels(image):
        count = image.width * image.height
        if count <= 0 or count > MAX_IMAGE_PIXELS:
            raise MediaError(f'Image validation incomplete: frame exceeds the {MAX_IMAGE_PIXELS:,}-pixel limit. '
                             'Use a smaller image or separately reviewed output.')
        return count

    try:
        with warnings.catch_warnings():
            warnings.simplefilter('error', Image.DecompressionBombWarning)
            with Image.open(path) as image:
                expected = {'.png': 'PNG', '.jpg': 'JPEG', '.jpeg': 'JPEG', '.webp': 'WEBP', '.gif': 'GIF'}
                if image.format != expected[path.suffix.lower()]:
                    raise MediaError('Image bytes do not match the filename format. Re-export in the requested format.')
                pixels(image)
                frame_count = _gif_frames(path) if image.format == 'GIF' else None
                image.verify()
            # verify checks structure and can close the decoder. Reopen and
            # decode every frame; valid headers/CRCs alone do not prove pixels.
            with Image.open(path) as image:
                total = 0
                for index in range(MAX_IMAGE_FRAMES):
                    if index:
                        try:
                            image.seek(index)
                        except EOFError:
                            if frame_count is not None and index != frame_count:
                                raise MediaError('Incomplete GIF frame decoding. Re-export the animation.')
                            return
                    total += pixels(image)
                    if total > MAX_IMAGE_TOTAL_PIXELS:
                        raise MediaError(f'Image validation incomplete: animation exceeds the '
                                         f'{MAX_IMAGE_TOTAL_PIXELS:,} decoded-pixel limit. '
                                         'Use a shorter or smaller animation, or separately reviewed output.')
                    image.load()
                try:
                    image.seek(MAX_IMAGE_FRAMES)
                except EOFError:
                    return
                raise MediaError(f'Image validation incomplete: animation exceeds the {MAX_IMAGE_FRAMES:,}-frame limit. '
                                 'Use a shorter animation or separately reviewed output.')
    except MediaError:
        raise
    except Exception as exc:
        raise MediaError('Invalid image output: pixels could not be fully decoded. Re-export or replace the image.') from exc


def validate_media(path):
    """Validate actual bytes, not a provider filename or declared MIME."""
    path = Path(path)
    if not path.is_file() or path.stat().st_size == 0:
        raise MediaError('Output is empty or missing')
    if path.suffix.lower() in {'.png', '.jpg', '.jpeg', '.webp', '.gif'}:
        _validate_image(path)
    elif path.suffix.lower() in {'.mp4', '.webm', '.mov'}:
        from .render import probe
        try:
            result = probe(path)
            if not any((s.get('codec_type') == 'video' for s in result.get('streams', []))):
                raise ValueError('No video stream')
        except Exception as exc:
            raise MediaError('Invalid video output') from exc
    else:
        raise MediaError('Completion requires a valid PNG/JPEG/WebP/GIF or MP4/WebM/MOV')


class ComfyUI:

    def __init__(self, base_url='http://127.0.0.1:8188', *, transport=None):
        try:
            url = urlsplit(base_url)
            if url.hostname != 'localhost' and (not ipaddress.ip_address(url.hostname).is_loopback):
                raise ValueError()
            if url.scheme != 'http' or url.username or url.password or (url.path not in {'', '/'}) or url.query or url.fragment:
                raise ValueError()
            self.base_url = base_url.rstrip('/')
        except (ValueError, TypeError):
            raise MediaError('ComfyUI must use an HTTP loopback origin') from None
        self.transport = transport

    def client(self):
        return httpx.AsyncClient(
            base_url=self.base_url,
            transport=self.transport,
            timeout=30,
            follow_redirects=False,
            trust_env=False,
        )

    async def submit(self, workflow, client_id):
        validate_workflow(workflow)
        async with self.client() as client:
            response = await client.post('/prompt', json={'prompt': workflow, 'client_id': client_id})
            response.raise_for_status()
            data = response.json()
        if data.get('error') or data.get('node_errors') or (not isinstance(data.get('prompt_id'), str)) or (not data['prompt_id']):
            raise MediaError('ComfyUI rejected workflow or returned no prompt ID')
        return data['prompt_id']

    async def outputs(self, backend_id, directory):
        if not isinstance(backend_id, str) or not backend_id or any((c not in 'abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_' for c in backend_id)):
            raise MediaError('Invalid backend ID')
        async with self.client() as client:
            response = await client.get('/history/' + backend_id)
            response.raise_for_status()
            entry = response.json().get(backend_id)
            if not entry:
                return None
            status = entry.get('status', {})
            if not isinstance(status, dict):
                raise MediaError('Invalid ComfyUI execution status')
            status_str = status.get('status_str')
            if isinstance(status_str, str) and status_str in {'error', 'failed'}:
                raise MediaError('ComfyUI execution failed')
            if ('completed' in status and not isinstance(status['completed'], bool)) or (
                'status_str' in status and not isinstance(status_str, str)
            ):
                raise MediaError('Invalid ComfyUI execution status')
            # Partial history can contain preview files. Explicit incomplete
            # evidence takes precedence over outputs, including conflicting
            # success fields. Status-absent legacy history remains supported.
            if status.get('completed') is False or (
                'status_str' in status and status_str != 'success'
            ):
                return None
            descriptors = []
            for node in entry.get('outputs', {}).values():
                for kind in ('images', 'videos', 'gifs'):
                    descriptors.extend(node.get(kind, []))
            if not descriptors:
                if status.get('completed'):
                    raise MediaError('ComfyUI completed without media outputs')
                return None
            if len(descriptors) > 32:
                raise MediaError('Too many provider outputs')
            paths = []
            for index, item in enumerate(descriptors):
                name = item.get('filename', '')
                folder = item.get('subfolder', '')
                typ = item.get('type', '')
                if not name or PurePosixPath(name).name != name or '\\' in name or PurePosixPath(folder).is_absolute() or ('..' in PurePosixPath(folder).parts) or ('\\' in folder) or (typ not in {'output', 'temp'}):
                    raise MediaError('Invalid provider output descriptor')
                path = Path(directory) / f'{index}{Path(name).suffix.lower()}'
                async with client.stream(
                    'GET',
                    '/view',
                    params={'filename': name, 'subfolder': folder, 'type': typ},
                ) as download:
                    download.raise_for_status()
                    total = 0
                    with path.open('xb') as output:
                        async for chunk in download.aiter_bytes():
                            total += len(chunk)
                            if total > 512 * 1024 * 1024:
                                raise MediaError('Provider output exceeds 512 MiB')
                            output.write(chunk)
                validate_media(path)
                paths.append(path)
            return paths


def svd_workflow(checkpoint, reference_image):
    """Reviewable installed built-in SVD path; constructing it performs no I/O.

    The operator must supply an existing backend checkpoint and input image,
    verify resources, and approve this concrete workflow before submission.
    Installed source qualification is not evidence that those prerequisites exist.
    """
    workflow = {
        '1': {'class_type': 'ImageOnlyCheckpointLoader', 'inputs': {'ckpt_name': checkpoint}},
        '2': {'class_type': 'LoadImage', 'inputs': {'image': reference_image}},
        '3': {
            'class_type': 'SVD_img2vid_Conditioning',
            'inputs': {
                'clip_vision': ['1', 1],
                'init_image': ['2', 0],
                'vae': ['1', 2],
                'width': 1024,
                'height': 576,
                'video_frames': 14,
                'motion_bucket_id': 127,
                'fps': 6,
                'augmentation_level': 0.0,
            },
        },
        '4': {'class_type': 'VideoLinearCFGGuidance', 'inputs': {'model': ['1', 0], 'min_cfg': 1.0}},
        '5': {
            'class_type': 'KSampler',
            'inputs': {
                'model': ['4', 0],
                'seed': 42,
                'steps': 20,
                'cfg': 2.5,
                'sampler_name': 'euler',
                'scheduler': 'karras',
                'denoise': 1.0,
                'positive': ['3', 0],
                'negative': ['3', 1],
                'latent_image': ['3', 2],
            },
        },
        '6': {'class_type': 'VAEDecode', 'inputs': {'samples': ['5', 0], 'vae': ['1', 2]}},
        '7': {
            'class_type': 'SaveWEBM',
            'inputs': {
                'images': ['6', 0],
                'filename_prefix': 'Dream/SVD',
                'codec': 'vp9',
                'fps': 6,
                'crf': 28,
            },
        },
    }
    return validate_workflow(workflow)
