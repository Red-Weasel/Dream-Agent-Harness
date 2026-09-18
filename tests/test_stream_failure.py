"""A crashed engine must be reported as a crashed engine, not a network glitch."""
import httpx
from dream.core.backends.openai_compat import _stream_failure


def test_incomplete_chunked_read_names_the_server_death(monkeypatch):
    from dream.local import machx
    monkeypatch.setattr(machx, "is_serving", lambda *a, **k: False)
    msg = _stream_failure("MachX", httpx.RemoteProtocolError(
        "peer closed connection without sending complete message body (incomplete chunked read)"))
    assert "no longer running" in msg
    assert "DEVICE_LOST" in msg
    assert "machx.log" in msg


def test_server_still_up_says_so(monkeypatch):
    from dream.local import machx
    monkeypatch.setattr(machx, "is_serving", lambda *a, **k: True)
    msg = _stream_failure("MachX", httpx.RemoteProtocolError("incomplete chunked read"))
    assert "still up" in msg


def test_other_http_errors_are_unchanged():
    msg = _stream_failure("MachX", httpx.ConnectTimeout("timed out"))
    assert "request failed" in msg and "ConnectTimeout" in msg
