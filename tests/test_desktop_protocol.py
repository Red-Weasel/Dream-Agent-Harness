"""Boundary checks for the native desktop, without a display or model."""
import json
from pathlib import Path
from unittest.mock import Mock

import pytest

from dream.desktop.protocol import browser_address, session_address
from dream.desktop import launcher


@pytest.mark.parametrize('raw, expected', [
    ('example.com/path', 'https://example.com/path'),
    ('localhost:3000', 'http://localhost:3000'),
    ('127.0.0.1:8080/a', 'http://127.0.0.1:8080/a'),
    ('[::1]:3000', 'http://[::1]:3000'),
    ('https://example.com/a?q=b', 'https://example.com/a?q=b'),
])
def test_normalize_browser_url(raw, expected):
    assert browser_address(raw) == expected


@pytest.mark.parametrize('raw', [
    'javascript:alert(1)', 'file:///etc/passwd', 'data:text/html,hi',
    'https://user:password@example.com', 'https://example.com:bad',
    'hello world', '', 'http://', 'https://example.com\n.evil.test',
])
def test_browser_refuses_non_web_or_ambiguous_input(raw):
    with pytest.raises(ValueError):
        browser_address(raw)


def test_discovery_belongs_to_current_child(tmp_path):
    path = tmp_path / 'session.json'
    path.write_text(json.dumps({'pid': 123, 'url': 'http://127.0.0.1:3232/?token=abc'}))
    assert session_address(path, 123) == ('http://127.0.0.1:3232', 'abc')
    assert session_address(path, 124) is None


@pytest.mark.parametrize('url', [
    'http://evil.test:3232/?token=abc',
    'http://127.0.0.1:3232/',
    'https://127.0.0.1:3232/?token=abc',
    'http://user@127.0.0.1:3232/?token=abc',
    'http://127.0.0.1:0/?token=abc',
])
def test_discovery_cannot_redirect_session_credentials(tmp_path, url):
    path = tmp_path / 'session.json'
    path.write_text(json.dumps({'url': url, 'pid': 123}))
    assert session_address(path, 123) is None


def test_discovery_tolerates_missing_partial_and_oversized_record(tmp_path):
    path = tmp_path / 'session.json'
    assert session_address(path) is None
    for raw in ('{', '[]', 'null', 'x' * 8193):
        path.write_text(raw)
        assert session_address(path) is None


def test_launcher_preserves_cli_arguments_and_harness_python(monkeypatch):
    monkeypatch.setattr(launcher, 'gtk_python', lambda: '/usr/bin/python3')
    monkeypatch.setenv('DISPLAY', ':1')
    call = Mock(return_value=Mock(wait=Mock(return_value=0)))
    monkeypatch.setattr(launcher.subprocess, 'Popen', call)
    assert launcher.launch(['--provider', 'codex', '--workspace', '/tmp/my project']) == 0
    args = call.call_args.args[0]
    assert args[:3] == ['/usr/bin/python3', '-m', 'dream.desktop.window']
    assert args[args.index('--python') + 1] == launcher.sys.executable
    assert args[args.index('--') + 1:] == ['--provider', 'codex', '--workspace', '/tmp/my project']
    assert call.call_args.kwargs['env']['PYTHONPATH'].split(':')[0] == str(Path(launcher.__file__).resolve().parents[2])
    assert call.call_args.kwargs['start_new_session'] is True


def test_interrupting_launcher_preserves_native_child(monkeypatch, capsys):
    monkeypatch.setattr(launcher, 'gtk_python', lambda: '/usr/bin/python3')
    monkeypatch.setenv('DISPLAY', ':1')
    child = Mock(wait=Mock(side_effect=KeyboardInterrupt))
    monkeypatch.setattr(launcher.subprocess, 'Popen', Mock(return_value=child))
    assert launcher.launch([]) == 130
    child.kill.assert_not_called()
    child.terminate.assert_not_called()
    assert 'still open' in capsys.readouterr().err


def test_missing_native_dependencies_is_actionable(monkeypatch, capsys):
    monkeypatch.setattr(launcher, 'gtk_python', lambda: None)
    assert launcher.launch([]) == 1
    output = capsys.readouterr().err
    assert 'gir1.2-webkit2-4.1' in output and 'dream' in output
