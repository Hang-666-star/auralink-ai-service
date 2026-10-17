"""Observed non-answer provider data is liveness, never answer/source evidence."""
import asyncio
import json

import httpx
import pytest

from app.assistant.execution import Runtime
from test_assistant_contract import command, config


def frame(delta=None, finish=None):
    return ("data: " + json.dumps({"choices": [{"delta": delta or {}, "finish_reason": finish}]}) + "\n\n").encode()


class Scheduled(httpx.AsyncByteStream):
    def __init__(self, items):
        self.items = items
        self.closed = False

    async def __aiter__(self):
        for delay, chunk in self.items:
            await asyncio.sleep(delay)
            yield chunk

    async def aclose(self):
        self.closed = True


async def execute(config, items, cancel=False):
    state, seen = Runtime(), []
    stream = Scheduled(items)

    def handler(request):
        seen.append(json.loads(request.content))
        return httpx.Response(200, headers={"content-type": "text/event-stream"}, stream=stream)

    state.client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    req = command(state)
    attempt = state.admit(req)
    events = []
    async for event in state.stream(req, state.model("text"), attempt):
        events.append(event)
        if cancel and event.startswith("event: heartbeat"):
            attempt["cancelled"].set()
    await state.close()
    assert len(seen) == 1 and stream.closed
    return events, attempt


@pytest.mark.asyncio
async def test_observed_reasoning_keeps_stream_alive_then_answer_without_leaking_reasoning(config):
    secret_reasoning = "private-reasoning-synthetic-sentinel"
    items = [(0.04, frame({"reasoning_content": secret_reasoning})) for _ in range(5)]
    items += [(0, frame({"content": "山水"})), (0, frame(finish="stop")), (0, b"data: [DONE]\n\n")]
    events, attempt = await execute(config, items)
    assert attempt["state"] == "COMPLETED"
    assert any(event.startswith("event: heartbeat") for event in events)
    assert secret_reasoning not in "".join(events)
    assert len([event for event in events if event.startswith("event: delta")]) == 1
    assert events[-1].startswith("event: done")


@pytest.mark.asyncio
async def test_activity_is_rate_limited_not_one_event_per_reasoning_fragment(config):
    events, _ = await execute(config, [(0, frame({"reasoning_content": "x"})) for _ in range(100)] + [(0, frame(finish="stop")), (0, b"data: [DONE]\n\n")])
    activity = [event for event in events if event.startswith("event: heartbeat")]
    assert len(activity) == 1
    assert activity[0] == 'event: heartbeat\ndata: {"kind":"provider_activity"}\n\n'


@pytest.mark.asyncio
async def test_role_is_valid_activity_but_empty_and_unknown_deltas_are_not(config):
    events, _ = await execute(config, [(0, frame({})), (0, frame({"unknown": "not-activity"})), (0, frame({"role": "assistant"})), (0, frame(finish="stop")), (0, b"data: [DONE]\n\n")])
    assert len([event for event in events if event.startswith("event: heartbeat")]) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("delta", [{"reasoning_content": 42}, {"role": "user"}, {"reasoning_content": ["private"]}])
async def test_malformed_activity_never_forwarded(config, delta):
    events, attempt = await execute(config, [(0, frame(delta))])
    assert attempt["state"] == "FAILED"
    assert len(events) == 1 and '"code":"UPSTREAM_INVALID_FRAME"' in events[0]


@pytest.mark.asyncio
@pytest.mark.parametrize("items", [[(0.2, frame({"content": "late"}))], [(0.025, b": comment\n\n") for _ in range(10)]])
async def test_silence_and_comments_still_expire_without_fabricated_activity(config, items):
    events, _ = await execute(config, items)
    assert len(events) == 1 and '"code":"UPSTREAM_TIMEOUT"' in events[0]


@pytest.mark.asyncio
async def test_continuous_reasoning_does_not_extend_total_deadline(config):
    events, attempt = await execute(config, [(0.04, frame({"reasoning_content": "x"})) for _ in range(30)])
    assert any(event.startswith("event: heartbeat") for event in events)
    assert '"code":"UPSTREAM_TIMEOUT"' in events[-1]
    assert attempt["state"] == "FAILED"
    assert not any(event.startswith("event: done") for event in events)


@pytest.mark.asyncio
async def test_partial_answer_then_eof_is_not_success(config):
    events, attempt = await execute(config, [(0, frame({"reasoning_content": "private"})), (0, frame({"content": "partial"}))])
    assert attempt["state"] == "FAILED"
    assert '"code":"UPSTREAM_INCOMPLETE"' in events[-1]
    assert not any(event.startswith("event: done") for event in events)


@pytest.mark.asyncio
async def test_cancel_during_reasoning_closes_response_without_retry(config):
    events, attempt = await execute(config, [(0.04, frame({"reasoning_content": "private"})) for _ in range(30)], cancel=True)
    assert attempt["state"] == "CANCELLED"
    assert '"code":"CANCELLED"' in events[-1]
    assert len([event for event in events if event.startswith("event: heartbeat")]) == 1
