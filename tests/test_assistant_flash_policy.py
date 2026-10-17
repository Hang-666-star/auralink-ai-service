"""Offline exact-route configuration and documented stream-shape regressions."""
import json

import httpx
import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from app.assistant.config import Model
from app.assistant.execution import Runtime
from app.assistant.providers import provider_body
from app.assistant.schemas import Execution
from test_assistant_contract import Bytes, command, config


def route(**updates):
    value = dict(id="text", provider="dashscope", model="qwen3.8-flash",
                 baseUrl="https://dashscope.aliyuncs.com/compatible-mode/v1",
                 keyFile="/synthetic/no-real-key")
    value.update(updates)
    return Model.model_validate(value)


def test_exact_route_opt_in_changes_only_one_top_level_parameter():
    req = Execution.model_validate(dict(contractVersion=1, runId="00000000-0000-4000-8000-000000000001",
        attemptId="00000000-0000-4000-8000-000000000002", fence=1, configSha256="0" * 64,
        purpose="CHAT", modelKey="text", messages=[{"role": "user", "content": "合成作品，作者待核实"}],
        budgets={"totalMs": 90000, "maxOutputTokens": 4096}))
    original = provider_body(req, route())
    changed = provider_body(req, route(thinkingMode="disabled"))
    assert changed == {**original, "enable_thinking": False}
    assert changed["max_tokens"] == 4096
    assert "extra_body" not in changed
    assert req.budgets.totalMs == 90000
    vl = provider_body(req, route(id="vision", model="qwen3-vl-plus"))
    assert vl == {**original, "model": "qwen3-vl-plus"}


@pytest.mark.parametrize("updates", [
    {"model": "qwen-flash"}, {"model": "qwen3-flash"}, {"model": "qwen3-vl-plus"},
    {"provider": "unverified-proxy"}, {"model": "qwen3.8-flash-unknown-snapshot"},
])
def test_thinking_support_is_not_inferred_from_alias_or_other_provider(updates):
    with pytest.raises(ValidationError):
        route(**updates, thinkingMode="disabled")
    assert route(**updates).thinkingMode == "provider-default"


@pytest.mark.parametrize("mode", [True, False, 0, None, "enabled", "off"])
def test_malformed_or_unreviewed_modes_are_rejected(mode):
    with pytest.raises(ValidationError):
        route(thinkingMode=mode)


def test_settings_remain_private_not_per_execution_or_browser(config):
    state = Runtime()
    value = command(state).model_dump()
    value["enable_thinking"] = False
    with pytest.raises(ValidationError):
        Execution.model_validate(value)


def data(value):
    return ("data: " + json.dumps(value, ensure_ascii=False) + "\r\n\r\n").encode()


def delta(value, finish=None):
    return data({"choices": [{"delta": value, "index": 0, "finish_reason": finish}], "usage": None})


async def consume(config, chunks):
    value, path, _ = config
    value["models"][0].update(provider="dashscope", model="qwen3.8-flash", thinkingMode="disabled")
    value["ragflow"] = {"enabled": False}
    path.write_text(json.dumps(value))
    state = Runtime()
    seen = []

    def handle(request):
        seen.append(json.loads(request.content))
        return httpx.Response(200, headers={"content-type": "text/event-stream"}, stream=Bytes(chunks))

    state.client = httpx.AsyncClient(transport=httpx.MockTransport(handle))
    req = command(state)
    attempt = state.admit(req)
    events = [x async for x in state.stream(req, state.model("text"), attempt)]
    await state.close()
    assert len(seen) == 1
    assert seen[0]["enable_thinking"] is False
    return events, attempt


@pytest.mark.asyncio
async def test_documented_shapes_split_utf8_nulls_usage_and_done_not_lost(config):
    # Synthetic sentinel only; real reasoning is never a test fixture or output.
    raw = delta({"role": "assistant", "content": None, "reasoning_content": ""})
    raw += delta({"content": None, "reasoning_content": "SYNTHETIC_PRIVATE_SENTINEL"})
    raw += delta({"content": "作者待核实。", "reasoning_content": None})
    raw += delta({"content": "", "reasoning_content": None}, "stop")
    raw += data({"choices": [], "usage": {"prompt_tokens": 12, "completion_tokens": 8,
        "total_tokens": 20, "completion_tokens_details": {"reasoning_tokens": 5}}})
    raw += b"data: [DONE]\r\n\r\n"
    events, attempt = await consume(config, [raw[i:i+1] for i in range(len(raw))])
    assert attempt["state"] == "COMPLETED"
    assert '"text":"作者待核实。"' in "".join(events)
    assert '"total_tokens":20' in "".join(events)
    assert "SYNTHETIC_PRIVATE_SENTINEL" not in "".join(events)
    assert "reasoning_tokens" not in "".join(events)
    assert events[-1].startswith("event: done")


@pytest.mark.asyncio
@pytest.mark.parametrize("raw,code", [
    (data({"error": {"code": "synthetic"}}), "UPSTREAM_ERROR"),
    (delta({"content": "partial"}), "UPSTREAM_INCOMPLETE"),
    (delta({"content": "partial"}, "stop"), "UPSTREAM_INCOMPLETE"),
    (delta({}, "tool_calls") + b"data: [DONE]\n\n", "UPSTREAM_INCOMPLETE"),
    (delta({"content": ["unexpected"]}), "UPSTREAM_INVALID_FRAME"),
])
async def test_error_missing_completion_and_unsupported_shapes_never_pass(config, raw, code):
    events, attempt = await consume(config, [raw])
    assert attempt["state"] == "FAILED"
    assert code in events[-1]
    assert not any(x.startswith("event: done") for x in events)


def test_invalid_override_does_not_change_critic_health(config):
    from app.main import app
    with TestClient(app) as client:
        before = client.get("/health").json()
    value, path, _ = config
    value["models"][1]["thinkingMode"] = "disabled"
    path.write_text(json.dumps(value))
    with TestClient(app) as client:
        assert client.get("/health").json() == before
        assert client.get("/internal/v1/assistant/models").json()["detail"] == "ASSISTANT_MISCONFIGURED"
