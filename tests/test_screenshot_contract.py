"""Screenshot schemas and native argument checks precede preview work."""
from types import SimpleNamespace

import pytest

from dream.core.tool_validation import validate_arguments
from dream.tools import studio


BAD = [
    ('save', {'steps': [{}]}, 'exactly one'),
    ('save', {'steps': [{}], 'save_path': 'x.png', 'in_memory_png_key': 'key'}, 'exactly one'),
    ('save', {'steps': [{}], 'save_path': 'private-invalid-extension'}, 'save_path'),
    ('save', {'steps': [{}], 'save_path': 'x.png\n'}, 'save_path'),
    ('save', {'steps': [{}], 'save_path': '.png'}, 'save_path'),
    ('save', {'steps': [{}], 'save_path': 'x.png/'}, 'save_path'),
    ('save', {'steps': [{}], 'save_path': 'x.png//'}, 'save_path'),
    ('save', {'steps': [{}], 'save_path': 'x.png/.'}, 'save_path'),
    ('save', {'steps': [], 'save_path': 'x.png'}, 'steps'),
    ('save', {'steps': [{}] * 101, 'save_path': 'x.png'}, '100'),
    ('save', {'steps': [{}], 'save_path': 123}, 'save_path'),
    ('save', {'steps': [{}], 'in_memory_png_key': True}, 'in_memory_png_key'),
    ('save', {'steps': [{}], 'save_path': ''}, 'save_path'),
    ('save', {'steps': [{'code': 123}], 'save_path': 'x.png'}, 'code'),
    ('save', {'steps': [{'code': None}], 'save_path': 'x.png'}, 'code'),
    ('multi', {'steps': [{}]}, 'code'),
    ('multi', {'steps': [{'code': ''}]}, 'code'),
    ('multi', {'steps': [{'code': True}]}, 'code'),
    ('multi', {'steps': [{'code': '1'}] * 13}, '12'),
    ('multi', {'steps': [{'code': '1', 'delay': float('nan')}]}, 'delay'),
    ('multi', {'steps': [{'code': '1', 'delay': float('inf')}]}, 'delay'),
    ('multi', {'steps': [{'code': '1', 'delay': True}]}, 'delay'),
]
IDS = ['missing-destination', 'both-destinations', 'extension', 'trailing-newline',
       'extension-only', 'trailing-slash', 'trailing-slashes', 'trailing-dot',
       'empty-steps', 'excess-save-steps', 'numeric-destination',
       'boolean-key', 'empty-destination', 'numeric-code', 'null-code', 'missing-code',
       'empty-code', 'boolean-code', 'excess-multi-steps', 'nan-delay', 'infinite-delay', 'boolean-delay']


def tool(which):
    return studio.save_screenshot if which == 'save' else studio.multi_screenshot


@pytest.mark.parametrize('path,valid', [('shots/.hero.PNG', True), ('shots/.png', False),
    ('.jpeg', False), ('/tmp/a.JPEG', True), ('nested/' + 'x' * 10000, False)],
    ids=['nested-hidden', 'nested-extension-only', 'jpeg-extension-only', 'absolute', 'long-invalid'])
def test_destination_schema_handles_paths_and_long_invalid_names(path, valid):
    result = validate_arguments(studio.save_screenshot.input_schema,
        {'path': 'page.html', 'steps': [{}], 'save_path': path})
    assert (result is None) is valid


@pytest.mark.parametrize('which,args,field', BAD, ids=IDS)
def test_schema_rejects_malformed_capture_arguments(which, args, field):
    message = validate_arguments(tool(which).input_schema, {'path': 'page.html', **args})
    assert message is not None
    assert 'Nothing ran' in message and len(message) <= 300
    assert 'private-invalid-extension' not in message


@pytest.mark.parametrize('which,args,field', BAD, ids=IDS)
async def test_native_rejects_malformed_arguments_before_preview(monkeypatch, tmp_path, which, args, field):
    effects = []
    monkeypatch.setattr(studio, '_page', lambda args: (tmp_path / 'page.html', None))
    monkeypatch.setattr(studio, '_resolve', lambda value: tmp_path / value)
    async def unavailable(path):
        effects.append('load')
        return 'Synthetic preview unavailable'
    def preview():
        effects.append('preview')
        raise AssertionError('No capture before argument validation')
    monkeypatch.setattr(studio, '_ensure_loaded', unavailable)
    monkeypatch.setattr(studio, 'get_preview', preview)
    result = await tool(which).handler({'path': 'page.html', **args})
    assert result.get('is_error')
    text = result['content'][0]['text']
    assert field in text
    assert len(text) <= 300 and 'private-invalid-extension' not in text
    assert effects == []


@pytest.mark.parametrize('which,count', [('save', 1), ('save', 100), ('multi', 1), ('multi', 12)])
@pytest.mark.parametrize('extension', ['.png', '.jpg', '.jpeg', '.PNG', '.JpEg'])
async def test_valid_boundaries_reach_capture_without_retuning(monkeypatch, tmp_path, which, count, extension):
    args = {'path': 'page.html', 'steps': [{'code': '1', 'delay': -1}] * count}
    if which == 'save':
        args.update(save_path='shot' + extension, hq=True)
    assert validate_arguments(tool(which).input_schema, args) is None
    monkeypatch.setattr(studio, '_page', lambda args: (tmp_path / 'page.html', None))
    monkeypatch.setattr(studio, '_resolve', lambda value: tmp_path / value)
    monkeypatch.setattr(studio.config, 'SCREENSHOT_DIR', tmp_path / 'captures')
    calls = []
    async def loaded(path):
        calls.append('load')
    async def capture(steps, **kwargs):
        calls.append((steps, kwargs))
        return [kwargs['save_to']]
    monkeypatch.setattr(studio, '_ensure_loaded', loaded)
    monkeypatch.setattr(studio, 'get_preview', lambda: SimpleNamespace(screenshot=capture))
    result = await tool(which).handler(args)
    assert not result.get('is_error')
    assert calls[0] == 'load' and len(calls) == 2
    steps, kwargs = calls[1]
    assert len(steps) == count and all(s['delay'] == -1 and s['code'] == '1' for s in steps)
    assert kwargs['save_to'].suffix == (extension if which == 'save' else '.jpg')
    if which == 'save':
        assert kwargs['hq'] is True


async def test_plain_memory_capture_keeps_default_delay_and_png(monkeypatch, tmp_path):
    args = {'path': 'page.html', 'steps': [{}], 'in_memory_png_key': 'deck'}
    assert validate_arguments(studio.save_screenshot.input_schema, args) is None
    monkeypatch.setattr(studio, '_page', lambda args: (tmp_path / 'page.html', None))
    async def loaded(path):
        return None
    calls = []
    async def capture(steps, **kwargs):
        calls.append((steps, kwargs))
    monkeypatch.setattr(studio, '_ensure_loaded', loaded)
    monkeypatch.setattr(studio, 'get_preview', lambda: SimpleNamespace(screenshot=capture))
    result = await studio.save_screenshot.handler(args)
    assert not result.get('is_error')
    assert calls == [([{'code': None, 'delay': 200}], {'hq': True, 'key': 'deck'})]


async def test_valid_arguments_preserve_preview_failure(monkeypatch, tmp_path):
    monkeypatch.setattr(studio, '_page', lambda args: (tmp_path / 'page.html', None))
    monkeypatch.setattr(studio, '_resolve', lambda value: tmp_path / value)
    async def unavailable(path):
        return 'Synthetic preview unavailable'
    monkeypatch.setattr(studio, '_ensure_loaded', unavailable)
    result = await studio.save_screenshot.handler({'path': 'page.html', 'steps': [{}], 'save_path': 'x.png'})
    assert result.get('is_error') and result['content'][0]['text'] == 'Synthetic preview unavailable'


@pytest.mark.parametrize('which', ['save', 'multi'])
@pytest.mark.parametrize('multimodal', [True, False])
async def test_capture_guidance_matches_session_image_input(monkeypatch, tmp_path, which, multimodal):
    from dream.tools.context import bind_context
    monkeypatch.setattr(studio, '_page', lambda args: (tmp_path / 'page.html', None))
    monkeypatch.setattr(studio.config, 'SCREENSHOT_DIR', tmp_path / 'captures')
    async def loaded(path):
        return None
    async def capture(steps, **kwargs):
        return [tmp_path / 'shot.png']
    monkeypatch.setattr(studio, '_ensure_loaded', loaded)
    monkeypatch.setattr(studio, 'get_preview', lambda: SimpleNamespace(screenshot=capture))
    args = {'path': 'page.html', 'steps': [{'code': '1'}]}
    if which == 'save':
        args['save_path'] = 'shot.png'
    with bind_context(SimpleNamespace(workspace=tmp_path, multimodal=multimodal)):
        result = await tool(which).handler(args)
    text = result['content'][0]['text']
    assert not result.get('is_error')
    assert 'not visual evidence' in text
    if multimodal:
        assert 'Call `see`' in text
        assert 'tool_schema(name="see")' in text
    else:
        assert 'image input is disabled' in text
        assert 'Call `see`' not in text


async def test_disabled_see_refuses_before_resolving_private_file(monkeypatch, tmp_path):
    from dream.tools import vision
    from dream.tools.context import bind_context
    def forbidden(path):
        raise AssertionError('Disabled image input must not read the requested path')
    monkeypatch.setattr(vision, '_resolve', forbidden)
    with bind_context(SimpleNamespace(workspace=tmp_path, multimodal=False)):
        result = await vision.see.handler({'path': 'private.png'})
    assert result.get('is_error') is True
    assert 'disabled' in result['content'][0]['text']


@pytest.mark.parametrize('multimodal', [True, False])
async def test_user_view_guidance_matches_session_image_input(monkeypatch, tmp_path, multimodal):
    from dream.tools.context import bind_context
    async def snapshot(*args):
        return '<html><body>fixture</body></html>'
    async def loaded(path):
        return None
    async def capture(*args, **kwargs):
        return [tmp_path / 'shot.png']
    monkeypatch.setattr(studio, 'studio', lambda: SimpleNamespace(ask_frame=snapshot))
    monkeypatch.setattr(studio, 'get_preview', lambda: SimpleNamespace(load=loaded, screenshot=capture))
    monkeypatch.setattr(studio.config, 'SCREENSHOT_DIR', tmp_path / 'captures')
    with bind_context(SimpleNamespace(workspace=tmp_path, multimodal=multimodal)):
        result = await studio.screenshot_user_view.handler({})
    text = result['content'][0]['text']
    assert not result.get('is_error')
    assert 'not visual evidence' in text
    assert ('Call `see`' in text) is multimodal
    assert ('image input is disabled' in text) is (not multimodal)
