"""A successful private preview explicitly distinguishes user presentation."""
from types import SimpleNamespace
from dream.tools.context import bind_context
from dream.tools import studio


async def test_private_preview_explains_user_has_not_been_shown_artifact(tmp_path, monkeypatch):
    path = tmp_path / 'preview.html'
    path.write_text('<h1>Draft</h1>')
    async def load(path):
        return []
    monkeypatch.setattr(studio, '_load', load)
    emitted = []
    with bind_context(SimpleNamespace(workspace=tmp_path, emit=emitted.append)):
        result = await studio.show_html.handler({'path': 'preview.html'})
    text = result['content'][0]['text']
    assert not result.get('is_error')
    assert "not shown in the user's Studio" in text
    assert 'show_to_user' in text
    assert emitted == []
