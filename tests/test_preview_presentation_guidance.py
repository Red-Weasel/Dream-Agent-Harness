"""A hidden-frame preview says plainly what the user can see.

Changed by DREAM-104 (owner request): the user's Studio pane follows the model's hidden
frame, so with Studio open a `show_html` is mirrored and the result says the user sees it
too; without a Studio server it says the user sees nothing. Before, the result called
the preview private in both cases and the model never surfaced its work.
"""
from types import SimpleNamespace

from dream.tools import studio
from dream.tools.context import bind_context, set_studio


async def _preview(tmp_path, monkeypatch, panel):
    path = tmp_path / 'preview.html'
    path.write_text('<h1>Draft</h1>')

    async def load(path):
        return []
    monkeypatch.setattr(studio, '_load', load)
    emitted = []
    set_studio(panel)
    try:
        with bind_context(SimpleNamespace(workspace=tmp_path, emit=emitted.append)):
            result = await studio.show_html.handler({'path': 'preview.html'})
    finally:
        set_studio(None)
    assert not result.get('is_error')
    return result['content'][0]['text'], emitted


async def test_preview_without_studio_says_the_user_sees_nothing(tmp_path, monkeypatch):
    text, emitted = await _preview(tmp_path, monkeypatch, None)
    assert 'Studio is not open' in text and 'sees nothing' in text
    assert emitted == []


async def test_preview_with_studio_open_says_the_user_sees_it_too(tmp_path, monkeypatch):
    text, emitted = await _preview(tmp_path, monkeypatch, SimpleNamespace(follow_model_view=True, client_count=1))
    assert 'Studio pane shows it too' in text and 'steer' in text
    (event,) = emitted
    assert event.kind == 'studio' and event.data['op'] == 'show' and event.data['source'] == 'mirror'


async def test_preview_with_following_off_names_show_to_user(tmp_path, monkeypatch):
    text, emitted = await _preview(tmp_path, monkeypatch, SimpleNamespace(follow_model_view=False))
    assert 'turned off' in text and 'show_to_user' in text
    assert emitted == []
