"""Media delivery requires bounded pixel decoding, without rendering or inference."""
import asyncio
import struct
import zlib

import pytest
from PIL import Image

from dream.media import providers
from dream.media.providers import validate_media
from dream.media.service import MediaService
from dream.media.store import MediaError


def _chunk(kind, data):
    return (struct.pack('!I', len(data)) + kind + data
            + struct.pack('!I', zlib.crc32(kind + data) & 0xffffffff))


def _broken_png():
    return (b'\x89PNG\r\n\x1a\n'
            + _chunk(b'IHDR', struct.pack('!IIBBBBB', 1, 1, 8, 2, 0, 0, 0))
            + _chunk(b'IDAT', b'invalid compressed pixels') + _chunk(b'IEND', b''))


def _animation(path, *, format='PNG', frames=3):
    images = [Image.new('RGB', (3, 2), (index * 70, 0, 0)) for index in range(frames)]
    images[0].save(path, format=format, save_all=True, append_images=images[1:], duration=100, loop=0)


def _break_later_png_frame(data):
    chunks, offset = [data[:8]], 8
    while offset < len(data):
        size = struct.unpack('!I', data[offset:offset + 4])[0]
        kind = data[offset + 4:offset + 8]
        body = data[offset + 8:offset + 8 + size]
        if kind == b'fdAT':
            body = body[:4] + b'invalid later compressed pixels'
        chunks.append(_chunk(kind, body))
        offset += size + 12
    return b''.join(chunks)


def test_crc_valid_png_with_undecodable_pixels_is_rejected(tmp_path):
    path = tmp_path / 'bad.png'
    path.write_bytes(_broken_png())
    with Image.open(path) as image:
        image.verify()  # The former structural-only check accepts this fixture.
    with pytest.raises(MediaError, match='decode|decodable'):
        validate_media(path)


def test_invalid_later_animation_frame_is_rejected(tmp_path):
    path = tmp_path / 'later.png'
    _animation(path)
    path.write_bytes(_break_later_png_frame(path.read_bytes()))
    with Image.open(path) as image:
        image.load()  # The first frame is valid; checking only it is insufficient.
    with pytest.raises(MediaError, match='decode|decodable'):
        validate_media(path)


@pytest.mark.parametrize('suffix,format', [('png', 'PNG'), ('jpg', 'JPEG'), ('jpeg', 'JPEG'),
                                         ('webp', 'WEBP'), ('gif', 'GIF')])
def test_decodable_static_formats_remain_accepted(tmp_path, suffix, format):
    path = tmp_path / ('image.' + suffix)
    Image.new('RGB', (3, 2), 'blue').save(path, format=format)
    validate_media(path)


@pytest.mark.parametrize('suffix,format', [('png', 'PNG'), ('webp', 'WEBP'), ('gif', 'GIF')])
def test_decodable_animation_remains_accepted(tmp_path, suffix, format):
    path = tmp_path / ('animated.' + suffix)
    _animation(path, format=format)
    validate_media(path)


def test_pixel_bound_rejects_before_decode(tmp_path, monkeypatch):
    path = tmp_path / 'image.png'
    Image.new('RGB', (3, 2)).save(path)
    monkeypatch.setattr(providers, 'MAX_IMAGE_PIXELS', 5, raising=False)
    from PIL import PngImagePlugin
    def forbidden_load(*args, **kwargs):
        pytest.fail('Pixels decoded before checking the per-frame bound')
    monkeypatch.setattr(PngImagePlugin.PngImageFile, 'load', forbidden_load)
    with pytest.raises(MediaError, match='pixel|limit'):
        validate_media(path)


@pytest.mark.parametrize('bound,value', [('MAX_IMAGE_FRAMES', 2), ('MAX_IMAGE_TOTAL_PIXELS', 12)])
def test_animation_validation_cannot_silently_stop_at_limit(tmp_path, monkeypatch, bound, value):
    path = tmp_path / 'animation.png'
    _animation(path, frames=3)
    monkeypatch.setattr(providers, bound, value, raising=False)
    with pytest.raises(MediaError, match='limit|incomplete'):
        validate_media(path)


def test_undecodable_handoff_preserves_waiting_job_and_accepts_replacement(tmp_path):
    service = MediaService(tmp_path)
    project = service.store.create_project('Synthetic decode check')
    job = asyncio.run(service.execute('handoff', {'project_id': project['id'],
        'provider': 'chatgpt', 'prompt': 'A viewable image'}))
    path = tmp_path / 'image.png'
    path.write_bytes(_broken_png())
    with pytest.raises(MediaError, match='decode|decodable'):
        asyncio.run(service.execute('complete_handoff', {'job_id': job['id'], 'path': str(path)}))
    assert service.store.get_job(job['id']) == job
    assert service.store.list_assets(project['id']) == []
    Image.new('RGB', (3, 2), 'green').save(path)
    result = asyncio.run(service.execute('complete_handoff', {'job_id': job['id'], 'path': str(path)}))
    assert result['status'] == 'succeeded'
    stored = service.store.asset_path(result['result']['asset_ids'][0])
    with Image.open(stored) as image:
        image.load()
        assert image.size == (3, 2)


@pytest.mark.parametrize('suffix', ['png', 'jpg', 'jpeg', 'webp', 'gif'])
def test_unsupported_actual_image_format_cannot_masquerade_as_supported(tmp_path, suffix):
    path = tmp_path / ('renamed.' + suffix)
    Image.new('RGB', (3, 2), 'blue').save(path, format='TIFF')
    with pytest.raises(MediaError, match='format|match'):
        validate_media(path)


@pytest.mark.parametrize('damage', ['missing_trailer', 'missing_later_frame', 'semicolon_inside_incomplete_block'])
def test_gif_requires_complete_blocks_and_trailer(tmp_path, damage):
    path = tmp_path / 'animated.gif'
    _animation(path, format='GIF')
    data = path.read_bytes()
    assert data[-1:] == b';'
    if damage == 'missing_trailer':
        damaged = data[:-1]
    elif damage == 'missing_later_frame':
        # Discard the third image descriptor and its data, without manufacturing
        # a new valid shorter file. Pillow previously treated this EOF as done.
        descriptor = b'\x2c\x00\x00\x00\x00\x03\x00\x02\x00'
        damaged = data[:data.rindex(descriptor)]
    else:
        # A trailing semicolon is payload inside an unfinished comment subblock,
        # not a GIF trailer. A last-byte-only check would accept it.
        damaged = data[:-1] + b'\x21\xfe\x10;'
    path.write_bytes(damaged)
    with pytest.raises(MediaError, match='GIF|complete|truncat'):
        validate_media(path)


def test_gif_semicolon_in_complete_comment_is_not_a_trailer(tmp_path):
    path = tmp_path / 'comment.gif'
    _animation(path, format='GIF')
    data = path.read_bytes()
    path.write_bytes(data[:-1] + b'\x21\xfe\x01;\x00;')
    validate_media(path)


@pytest.mark.parametrize('bound,value', [('MAX_IMAGE_FRAMES', 2), ('MAX_IMAGE_TOTAL_PIXELS', 12)])
def test_gif_container_limits_are_checked_before_pixel_decode(tmp_path, monkeypatch, bound, value):
    path = tmp_path / 'animated.gif'
    _animation(path, format='GIF')
    monkeypatch.setattr(providers, bound, value)
    from PIL import GifImagePlugin
    def forbidden_load(*args, **kwargs):
        pytest.fail('GIF pixels decoded before container bounds were checked')
    monkeypatch.setattr(GifImagePlugin.GifImageFile, 'load', forbidden_load)
    with pytest.raises(MediaError, match='limit'):
        validate_media(path)


@pytest.mark.parametrize('kind', ['renamed_tiff', 'truncated_gif'])
def test_invalid_container_handoff_keeps_original_waiting_job(tmp_path, kind):
    service = MediaService(tmp_path)
    project = service.store.create_project('Synthetic container check')
    job = asyncio.run(service.execute('handoff', {'project_id': project['id'],
        'provider': 'chatgpt', 'prompt': 'A viewable image'}))
    path = tmp_path / ('image.png' if kind == 'renamed_tiff' else 'image.gif')
    if kind == 'renamed_tiff':
        Image.new('RGB', (3, 2)).save(path, format='TIFF')
    else:
        _animation(path, format='GIF')
        path.write_bytes(path.read_bytes()[:-1])
    original = path.read_bytes()
    with pytest.raises(MediaError):
        asyncio.run(service.execute('complete_handoff', {'job_id': job['id'], 'path': str(path)}))
    assert service.store.get_job(job['id']) == job
    assert service.store.list_assets(project['id']) == []
    assert path.read_bytes() == original
