"""Local generation can buffer a large tool call without a hidden profile cutoff."""
from dataclasses import replace
from unittest.mock import AsyncMock
import json
import httpx
import pytest
from dream import config
from dream.core.profiles import PROFILES
from dream.telemetry.runtime import RunMeter
from test_backend_resilience import _backend, _ScriptedClient, _sse


@pytest.mark.parametrize('local,explicit,expected',[(True,None,None),(True,12,12),(False,None,300),(False,12,12)])
async def test_connect_timeout_policy(local,explicit,expected,monkeypatch):
    backend=_backend()
    backend.profile=PROFILES['balanced']
    backend.provider.key='machx' if local else 'openai'
    backend.provider.base_url='http://127.0.0.1:1/v1' if local else 'https://provider.example/v1'
    monkeypatch.delenv('DREAM_LLM_READ_TIMEOUT_S',raising=False)
    monkeypatch.setattr(config,'LLM_READ_TIMEOUT_S',explicit)
    if explicit is not None: monkeypatch.setenv('DREAM_LLM_READ_TIMEOUT_S',str(explicit))
    monkeypatch.setattr(backend,'_probe_n_ctx',AsyncMock(return_value=65536))
    try:
        await backend.connect()
        assert backend._client.timeout.read==expected
        assert backend._client.timeout.connect==15
        assert backend._client.timeout.write==(300 if explicit is None else explicit)
        assert backend._client.timeout.pool==(300 if explicit is None else explicit)
    finally:
        await backend.disconnect()


async def test_stream_failure_retains_category_without_raw_response(tmp_path):
    backend=_backend()
    backend.profile=PROFILES['balanced']
    backend.runtime_meter=RunMeter('fixture',1,backend.profile,tmp_path/'failure.jsonl')
    backend._client=_ScriptedClient([[_sse({'choices':[{'delta':{'content':'partial'},'finish_reason':None}]}),httpx.ReadTimeout('secret payload must not be logged')]])
    events=[event async for event in backend.ask('fixture')]
    result=events[-1].data
    assert result['failure']['exception_type']=='ReadTimeout'
    assert result['failure']['stage']=='stream'
    records=[json.loads(line) for line in (tmp_path/'failure.jsonl').read_text().splitlines()]
    failure=next(row for row in records if row['event']=='request_failure')
    assert failure['exception_type']=='ReadTimeout'
    assert 'secret payload' not in json.dumps(failure)
    assert backend._client.attempts==1


@pytest.mark.parametrize('cap',[None,0.03])
async def test_real_buffered_stream_outlives_profile_idle_but_honors_explicit_cap(cap,tmp_path,monkeypatch):
    import asyncio
    from dream.core.inference_coordination import EndpointCoordinator
    requests=[]
    finished=asyncio.Event()
    async def serve(reader,writer):
        try:
            headers=await reader.readuntil(b'\r\n\r\n')
            length=next((int(line.split(b':',1)[1]) for line in headers.split(b'\r\n') if line.lower().startswith(b'content-length:')),0)
            await reader.readexactly(length)
            requests.append(1)
            first=b'data: {"choices":[{"delta":{"content":"working"},"finish_reason":null}]}\n\n'
            last=b'data: {"choices":[{"delta":{},"finish_reason":"stop"}]}\n\ndata: [DONE]\n\n'
            writer.write(b'HTTP/1.1 200 OK\r\nContent-Type: text/event-stream\r\nContent-Length: '+str(len(first+last)).encode()+b'\r\n\r\n'+first)
            await writer.drain()
            await asyncio.sleep(.12)
            writer.write(last);await writer.drain()
        finally:
            writer.close();await writer.wait_closed();finished.set()
    server=await asyncio.start_server(serve,'127.0.0.1',0)
    backend=_backend();backend.profile=replace(PROFILES['balanced'],idle_timeout_s=.03)
    backend.provider.base_url=f'http://127.0.0.1:{server.sockets[0].getsockname()[1]}/v1'
    backend._coordination_override=EndpointCoordinator('loopback:19435',root=tmp_path/'coord')
    monkeypatch.setattr(backend,'_probe_n_ctx',AsyncMock(return_value=65536))
    monkeypatch.setattr(config,'LLM_READ_TIMEOUT_S',cap)
    monkeypatch.delenv('DREAM_LLM_READ_TIMEOUT_S',raising=False)
    if cap is not None:monkeypatch.setenv('DREAM_LLM_READ_TIMEOUT_S',str(cap))
    try:
        await backend.connect()
        async def consume():
            async with backend._stream_with_retry({'model':'fixture','messages':[],'stream':True}) as response:
                return [line async for line in response.aiter_lines()]
        if cap is None:
            assert 'data: [DONE]' in await consume()
            assert backend.coordination_status()['state']=='idle'
        else:
            with pytest.raises(httpx.ReadTimeout):await consume()
            assert backend.coordination_status()['state']=='uncertain'
        assert requests==[1]
        await asyncio.wait_for(finished.wait(),1)
    finally:
        await backend.disconnect();server.close();await server.wait_closed()


@pytest.mark.parametrize('url,expected',[
    ('http://127.0.0.1:19435/v1',None),('http://[::1]:19435/v1',None),
    ('http://localhost:19435/v1',None),('https://127.provider.example/v1',300)])
async def test_custom_openai_endpoint_uses_canonical_local_identity(url,expected,monkeypatch):
    backend=_backend();backend.provider.key='openai';backend.provider.base_url=url
    backend.profile=PROFILES['balanced']
    monkeypatch.delenv('DREAM_LLM_READ_TIMEOUT_S',raising=False)
    monkeypatch.setattr(config,'LLM_READ_TIMEOUT_S',None)
    try:
        await backend.connect()
        assert backend.transport_status()['read_timeout_s']==expected
    finally:await backend.disconnect()
