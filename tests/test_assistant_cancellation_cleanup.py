"""ASGI cancellation must permit bounded upstream-response cleanup."""
import asyncio
import time
import anyio
import httpx
import pytest
from app.assistant.execution import Runtime
from test_assistant_contract import command, config
from test_assistant_provider_activity import frame

class CancellableStream(httpx.AsyncByteStream):
    def __init__(self, stuck=False):
        self.closed = False
        self.stuck = stuck
        self.read_closed = False
    async def __aiter__(self):
        try:
            yield frame({'reasoning_content': 'private-offline-sentinel'})
            await anyio.sleep(30)
        finally:
            await anyio.sleep(.01)
            self.read_closed = True
    async def aclose(self):
        await anyio.sleep(30 if self.stuck else .01)
        self.closed = True

@pytest.mark.asyncio
@pytest.mark.parametrize('stuck', [False, True])
async def test_asgi_scope_cancellation_closes_response_or_bounds_cleanup(config, stuck):
    state, calls, stream = Runtime(), [], CancellableStream(stuck)
    def handler(request):
        calls.append(1)
        return httpx.Response(200, headers={'content-type':'text/event-stream'}, stream=stream)
    state.client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    req=command(state);attempt=state.admit(req);start=time.monotonic();events=[]
    with anyio.CancelScope() as scope:
        async for event in state.stream(req,state.model('text'),attempt):
            events.append(event)
            scope.cancel()
    elapsed=time.monotonic()-start
    assert len(calls)==1
    assert attempt['state']=='INTERRUPTED'
    assert not any('private-offline-sentinel' in event or event.startswith('event: done') for event in events)
    assert stream.read_closed
    assert stream.closed is (not stuck)
    assert elapsed < 1.6
    if stuck: assert elapsed >= .9
    await state.close()
