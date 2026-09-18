"""Transport-only local coordination; all clients are fake, state temporary."""
import asyncio
import httpx
import pytest
from test_schema_deferral import _backend, _FakeClient, _text_round
from dream.core.inference_coordination import EndpointCoordinator
from dream.core.backends.openai_compat import _RequestFailed


def local_backend(tmp_path):
    b = _backend()
    b.provider.base_url = "http://127.0.0.1:19435/v1"
    b._coordination_override = EndpointCoordinator("loopback:19435", root=tmp_path / "coord")
    return b


async def test_completed_stream_releases_and_early_eof_fences(tmp_path):
    b = local_backend(tmp_path)
    b._client = _FakeClient([_text_round()])
    events = [e async for e in b.ask("hello")]
    assert not any(e.kind == "error" for e in events)
    assert b.coordination_status()["state"] == "idle"
    b._client = _FakeClient([["data: {}"]])
    events = [e async for e in b.ask("hello")]
    assert any(e.kind == "error" for e in events)
    assert b.coordination_status()["state"] == "uncertain"
    count = len(b._client.payloads)
    [e async for e in b.ask("retry")]
    assert len(b._client.payloads) == count
    status = b.coordination_status()
    b.reconcile_local_request(status["request_id"], confirmed_idle=True)
    assert b.coordination_status()["state"] == "idle"


async def test_timeout_after_dispatch_is_not_retried(tmp_path):
    b = local_backend(tmp_path)
    class Client:
        calls = 0
        async def post(self, *args, **kwargs):
            self.calls += 1
            raise httpx.ReadTimeout("server may still be working")
    b._client = Client()
    with pytest.raises(httpx.ReadTimeout):
        await b._post_with_retry({})
    assert b._client.calls == 1
    assert b.coordination_status()["state"] == "uncertain"


async def test_cancellation_fences_second_client_until_reconciled(tmp_path):
    a, b = local_backend(tmp_path), local_backend(tmp_path)
    entered = asyncio.Event()
    class Client:
        calls = 0
        async def post(self, *args, **kwargs):
            self.calls += 1
            entered.set()
            await asyncio.Event().wait()
    a._client = b._client = Client()
    task = asyncio.create_task(a._post_with_retry({}))
    await entered.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    with pytest.raises(_RequestFailed, match="uncertain"):
        await b._post_with_retry({})
    assert a._client.calls == 1


async def test_completed_http_error_does_not_fence(tmp_path):
    b = local_backend(tmp_path)
    class Client:
        async def post(self, *args, **kwargs):
            return httpx.Response(400, text="invalid request")
    b._client = Client()
    with pytest.raises(_RequestFailed, match="400"):
        await b._post_with_retry({})
    assert b.coordination_status()["state"] == "idle"


async def test_local_foreground_drains_active_idle_request(tmp_path):
    b = local_backend(tmp_path)
    b.enable_background_filing()
    b._idle_work.idle_grace = 0
    started, finish = asyncio.Event(), asyncio.Event()
    async def work():
        async with b._request_coordination():
            started.set()
            await finish.wait()
    b._idle_work.enqueue(work, label="fixture")
    await started.wait()
    foreground = asyncio.create_task(b.prepare_user_turn())
    await asyncio.sleep(.01)
    assert not foreground.done()
    finish.set()
    await foreground
    assert b.coordination_status()["state"] == "idle"
    await b.close_background()


async def test_gateway_timeout_is_not_retried_or_treated_as_idle(tmp_path):
    b = local_backend(tmp_path)
    class Client:
        calls = 0
        async def post(self, *args, **kwargs):
            self.calls += 1
            return httpx.Response(504, text='upstream timeout')
    b._client = Client()
    with pytest.raises(_RequestFailed, match='uncertain'):
        await b._post_with_retry({})
    assert b._client.calls == 1
    assert b.coordination_status()['state'] == 'uncertain'
