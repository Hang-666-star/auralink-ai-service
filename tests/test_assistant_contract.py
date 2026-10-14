"""Offline Assistant boundary tests. All upstreams are deterministic transports."""
import asyncio
import base64
import importlib
import io
import json
from pathlib import Path
from uuid import uuid4

from fastapi.testclient import TestClient
import httpx
from PIL import Image
import pytest

from app.assistant.config import Settings, load_configuration
from app.assistant.execution import Runtime
from app.assistant.prompts import parse_choices
from app.assistant.providers import ProviderFailure, sse_data, validate_images
from app.assistant.retrieval import retrieve
from app.assistant.schemas import Execution, Retrieval


@pytest.fixture
def config(monkeypatch, tmp_path):
    secret = tmp_path / "secret"
    secret.write_text("synthetic-test-secret-not-a-real-credential")
    secret.chmod(0o600)
    value = {"mode": "enabled", "allowLoopbackHttp": True,
        "models": [{"id": "text", "provider": "fixture-a", "model": "text-model", "baseUrl": "http://127.0.0.1:19001/v1", "keyFile": str(secret)},
                   {"id": "vision", "provider": "fixture-b", "model": "vision-model", "baseUrl": "http://127.0.0.1:19001/v1", "keyFile": str(secret), "capabilities": {"images": True}}],
        "ragflow": {"enabled": True, "baseUrl": "http://127.0.0.1:19002", "apiKeyFile": str(secret), "datasetIds": ["allowed"], "timeoutMs": 100},
        "limits": {"concurrency": 1, "idleMs": 100, "chatMs": 500, "summaryMs": 500}}
    path = tmp_path / "config.json"
    path.write_text(json.dumps(value))
    monkeypatch.setenv("AURALINK_ASSISTANT_CONFIG_FILE", str(path))
    monkeypatch.setenv("AURALINK_ASSISTANT_INTERNAL_TOKEN_FILE", str(secret))
    return value, path, secret


def command(state, **updates):
    value = {"contractVersion": 1, "runId": str(uuid4()), "attemptId": str(uuid4()), "fence": 1,
        "configSha256": state.digest, "purpose": "CHAT", "modelKey": "text", "messages": [{"role": "user", "content": "山水"}]}
    value.update(updates)
    return Execution.model_validate(value)


class Bytes(httpx.AsyncByteStream):
    def __init__(self, chunks, delay=0):
        self.chunks, self.delay = chunks, delay
    async def __aiter__(self):
        for chunk in self.chunks:
            if self.delay:
                await asyncio.sleep(self.delay)
            yield chunk


def frames(text="山水", done=True):
    content = ('data: ' + json.dumps({"choices": [{"delta": {"content": text}, "finish_reason": None}]}, ensure_ascii=False) + '\r\n\r\n').encode()
    chunks = [content[index:index + 1] for index in range(len(content))]
    chunks.append(b'data: {"choices":[{"delta":{},"finish_reason":"stop"}]}\n\n')
    if done:
        chunks.append(b'data: [DONE]\n\n')
    return chunks


def test_disabled_or_malformed_assistant_does_not_change_critic_health(monkeypatch, tmp_path):
    app = importlib.import_module("app.main").app
    with TestClient(app) as client:
        baseline = client.get("/health").json()
        assert client.get("/internal/v1/assistant/models").status_code == 503
    malformed = tmp_path / "bad.json"
    malformed.write_text('{"mode":"enabled","models":null}')
    monkeypatch.setenv("AURALINK_ASSISTANT_CONFIG_FILE", str(malformed))
    with TestClient(app) as client:
        assert client.get("/health").json() == baseline
        assert client.get("/internal/v1/assistant/models").json()["detail"] == "ASSISTANT_MISCONFIGURED"


def test_private_auth_and_model_catalog_do_not_reveal_secrets(config):
    app = importlib.import_module("app.main").app
    with TestClient(app) as client:
        assert client.get("/internal/v1/assistant/models").status_code == 401
        result = client.get("/internal/v1/assistant/models", headers={"Authorization": "Bearer " + config[2].read_text()})
    assert result.status_code == 200
    models = result.json()["models"]
    assert [x["id"] for x in models] == ["text", "vision"]
    assert [x["capabilities"]["images"] for x in models] == [False, True]
    assert all(x["evidence"] == "CONFIGURED_NOT_VERIFIED" for x in models)
    assert config[2].read_text() not in result.text and str(config[2]) not in result.text
    assert "127.0.0.1" not in result.text


@pytest.mark.parametrize("rag", [None, {"enabled": False},
    {"enabled": False, "baseUrl": "not-an-endpoint", "apiKeyFile": "/missing/disabled-rag-key", "datasetIds": []}])
def test_first_release_without_rag_dependencies_has_no_retrieval_network(config, monkeypatch, rag):
    data, path, secret = config
    if rag is None:
        data.pop("ragflow")
    else:
        data["ragflow"] = rag
    path.write_text(json.dumps(data))
    app = importlib.import_module("app.main").app
    seen = []

    async def forbidden(*args, **kwargs):
        seen.append("unexpected-upstream")
        raise AssertionError("Disabled RAG must never open an upstream request")

    # Exercise the real authenticated router, not only the helper's return value.
    monkeypatch.setattr(httpx.AsyncHTTPTransport, "handle_async_request", forbidden)
    headers = {"Authorization": "Bearer " + secret.read_text()}
    with TestClient(app) as client:
        registry = client.get("/internal/v1/assistant/models", headers=headers).json()
        assert registry["retrieval"]["state"] == "NOT_CONFIGURED"
        assert len(registry["models"]) == 2
        assert app.state.assistant_runtime.state == "READY"
        assert "__ragflow" not in app.state.assistant_runtime.keys
        for enabled, expected in [(False, "DISABLED"), (True, "NOT_CONFIGURED")]:
            response = client.post("/internal/v1/assistant/retrieval", headers=headers,
                json={"contractVersion": 1, "configSha256": registry["configSha256"],
                    "enabled": enabled, "query": "合成山水"})
            assert response.status_code == 200
            assert response.json()["state"] == expected
            assert response.json()["citations"] == []
    assert seen == []


@pytest.mark.asyncio
async def test_no_rag_chat_uses_only_explicit_model_route(config):
    data, path, _ = config
    data["ragflow"] = {"enabled": False}
    path.write_text(json.dumps(data))
    state = Runtime()
    seen = []

    def handler(request):
        seen.append((str(request.url), json.loads(request.content)))
        return httpx.Response(200, headers={"content-type": "text/event-stream"}, stream=Bytes(frames()))

    state.client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    req = command(state, retrievalStates={"artworks": "EMPTY", "ragflow": "DISABLED"})
    events = [event async for event in state.stream(req, state.model("text"), state.admit(req))]
    assert events[-1].startswith("event: done")
    assert len(seen) == 1
    assert seen[0][0] == "http://127.0.0.1:19001/v1/chat/completions"
    assert seen[0][1]["model"] == "text-model"
    await state.close()


def test_enabling_rag_still_requires_its_own_configuration(config):
    data, path, _ = config
    data["ragflow"] = {"enabled": True}
    path.write_text(json.dumps(data))
    assert load_configuration()[-1] == "MISCONFIGURED"


@pytest.mark.parametrize("change", ["unknown", "http", "key-permissions", "missing-token"])
def test_invalid_configuration_is_assistant_only(config, monkeypatch, change):
    data, path, secret = config
    if change == "unknown":
        data["QWEN_MODEL"] = "not-assistant-config"
    elif change == "http":
        data["models"][0]["baseUrl"] = "http://remote.example/v1"
    elif change == "key-permissions":
        secret.chmod(0o644)
    else:
        monkeypatch.delenv("AURALINK_ASSISTANT_INTERNAL_TOKEN_FILE")
    path.write_text(json.dumps(data))
    assert load_configuration()[-1] == "MISCONFIGURED"


def test_schema_refuses_client_urls_datasets_and_file_paths(config):
    state = Runtime()
    for addition in ({"baseUrl": "http://evil"}, {"datasetIds": ["private"]}, {"imageInputs": [{"path": "/root/private"}]}):
        with pytest.raises(ValueError):
            command(state, **addition)


def test_config_hash_change_and_explicit_model_selection(config):
    state = Runtime()
    assert state.model("vision").model == "vision-model"
    with pytest.raises(Exception) as error:
        state.model("unknown")
    assert error.value.status_code == 422
    with pytest.raises(Exception) as error:
        state.bind("0" * 64)
    assert error.value.status_code == 409


def test_image_capability_bytes_mime_and_actual_decoder(config):
    state = Runtime()
    stream = io.BytesIO()
    Image.new("RGB", (4, 5), "blue").save(stream, "PNG")
    data = base64.b64encode(stream.getvalue()).decode()
    req = command(state, messages=[{"role": "user", "content": "画", "images": [{"mimeType": "image/png", "dataBase64": data}]}])
    with pytest.raises(ProviderFailure, match="MODEL_IMAGES_UNSUPPORTED"):
        validate_images(req, state.model("text"), state.settings.limits)
    validate_images(req, state.model("vision"), state.settings.limits)
    req.messages[0].images[0].mimeType = "image/jpeg"
    with pytest.raises(ProviderFailure, match="INVALID_IMAGE"):
        validate_images(req, state.model("vision"), state.settings.limits)


@pytest.mark.asyncio
async def test_real_codec_split_utf8_crlf_multiline_and_limits():
    async def source(chunks):
        for chunk in chunks:
            yield chunk
    raw = "data: 山\r\ndata: 水\r\n\r\n".encode()
    assert [frame async for frame in sse_data(source([raw[i:i+1] for i in range(len(raw))]))] == ["山\n水"]
    with pytest.raises(ProviderFailure, match="UPSTREAM_FRAME_TOO_LARGE"):
        _ = [frame async for frame in sse_data(source([b"data: " + b"x" * 300000]))]


@pytest.mark.asyncio
async def test_single_call_stream_done_duplicate_guard_and_no_fallback(config):
    state = Runtime()
    seen = []
    def handler(request):
        seen.append(json.loads(request.content))
        return httpx.Response(200, headers={"content-type": "text/event-stream"}, stream=Bytes(frames()))
    state.client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    req = command(state)
    events = [e async for e in state.stream(req, state.model("text"), state.admit(req))]
    assert any('"text":"山水"' in event for event in events)
    assert events[-1].startswith("event: done")
    assert len(seen) == 1 and seen[0]["model"] == "text-model"
    with pytest.raises(Exception) as error:
        state.admit(req)
    assert error.value.status_code == 409 and len(seen) == 1
    await state.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("failure,expected", [("eof", "UPSTREAM_INCOMPLETE"), ("status", "UPSTREAM_REJECTED"), ("slow", "UPSTREAM_TIMEOUT")])
async def test_failed_stream_never_terminal_success_or_retried(config, failure, expected):
    state = Runtime()
    count = 0
    def handler(request):
        nonlocal count
        count += 1
        return httpx.Response(500 if failure == "status" else 200, headers={"content-type": "text/event-stream"},
            stream=Bytes(frames(done=failure != "eof"), delay=0.2 if failure == "slow" else 0))
    state.client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    req = command(state)
    events = [e async for e in state.stream(req, state.model("text"), state.admit(req))]
    assert events[-1].startswith("event: error") and expected in events[-1]
    assert not any(e.startswith("event: done") for e in events)
    assert count == 1
    await state.close()


@pytest.mark.asyncio
async def test_cancel_and_bounded_concurrency(config):
    state = Runtime()
    state.client = httpx.AsyncClient(transport=httpx.MockTransport(lambda _: httpx.Response(200, headers={"content-type": "text/event-stream"}, stream=Bytes(frames(), delay=0.05))))
    req = command(state)
    attempt = state.admit(req)
    with pytest.raises(Exception) as error:
        state.admit(command(state))
    assert error.value.status_code == 429
    async def consume():
        return [x async for x in state.stream(req, state.model("text"), attempt)]
    task = asyncio.create_task(consume())
    await asyncio.sleep(0.01)
    attempt["cancelled"].set()
    events = await task
    assert '"code":"CANCELLED"' in events[-1]
    assert state.active == 0
    await state.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("outcome,expected", [("ok", "AVAILABLE"), ("empty", "EMPTY"), ("forbidden", "FORBIDDEN"), ("wrong-dataset", "FORBIDDEN"), ("invalid", "UNAVAILABLE"), ("slow", "TIMEOUT")])
async def test_rag_contract_states_allowlist_and_projection(config, outcome, expected):
    state = Runtime()
    received = []
    async def handler(request):
        received.append(json.loads(request.content))
        if outcome == "slow":
            await asyncio.sleep(0.2)
        chunks = [{"id": "chunk", "document_id": "doc", "dataset_id": "other" if outcome == "wrong-dataset" else "allowed",
                   "document_keyword": "图谱", "content": "待核验 0", "internal_path": "/private/not-exported"}]
        return httpx.Response(403 if outcome == "forbidden" else 200,
            json={"code": 0, "data": {"chunks": [] if outcome == "empty" else "bad" if outcome == "invalid" else chunks}})
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        req = Retrieval(contractVersion=1, configSha256=state.digest, enabled=True, query="山水")
        response = await retrieve(client, req, state.settings, "synthetic-secret")
    assert response["state"] == expected and len(received) == 1
    assert received[0]["dataset_ids"] == ["allowed"]
    if expected == "AVAILABLE":
        citation = response["citations"][0]
        assert citation["type"] == "RAGFLOW" and citation["version"] is None
        assert citation["text"] == "待核验 0" and "internal_path" not in citation


def test_choice_schema_malformed_falls_back_without_call():
    valid = [{"id": "stage", "title": "阶段", "options": [{"id": "a", "label": "构图"}, {"id": "b", "label": "用墨"}]}]
    assert parse_choices("text\n```assistant-choices\n" + json.dumps(valid) + "\n```") == valid
    assert parse_choices("```assistant-choices\nnot-json\n```") == []
    assert parse_choices("```assistant-choices\n" + json.dumps(valid)) == []
    valid[0]["options"][1]["id"] = "a"
    assert parse_choices("```assistant-choices\n" + json.dumps(valid) + "\n```") == []


@pytest.mark.asyncio
async def test_cancel_before_dispatch_sends_zero_requests(config):
    state = Runtime()
    count = 0
    def handler(request):
        nonlocal count
        count += 1
        raise AssertionError("cancelled operation must not dispatch")
    state.client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    req = command(state)
    attempt = state.admit(req)
    attempt["cancelled"].set()
    events = [e async for e in state.stream(req, state.model("text"), attempt)]
    assert count == 0 and '"code":"CANCELLED"' in events[-1]
    await state.close()


def test_actual_asgi_execution_and_sanitized_validation_errors(config):
    app = importlib.import_module("app.main").app
    received = []
    def handler(request):
        received.append(json.loads(request.content))
        return httpx.Response(200, headers={"content-type": "text/event-stream"}, stream=Bytes(frames("实测")))
    headers = {"Authorization": "Bearer " + config[2].read_text()}
    with TestClient(app) as client:
        state = app.state.assistant_runtime
        client.portal.call(state.client.aclose)
        state.client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        req = command(state).model_dump(mode="json")
        response = client.post("/internal/v1/assistant/executions", json=req, headers=headers)
        assert response.status_code == 200 and "text/event-stream" in response.headers["content-type"]
        assert "实测" in response.text and "event: done" in response.text
        assert client.post("/internal/v1/assistant/executions", json=req, headers=headers).status_code == 409
        invalid = {**req, "clientCredential": "must-never-return-private-value"}
        response = client.post("/internal/v1/assistant/executions", json=invalid, headers=headers)
        assert response.status_code == 422 and "must-never-return" not in response.text
        assert len(received) == 1


def test_assistant_client_initialization_failure_does_not_abort_critic(config, monkeypatch):
    app = importlib.import_module("app.main").app
    async def failed_start(self):
        raise ValueError("synthetic-resource-failure")
    monkeypatch.setattr(Runtime, "start", failed_start)
    with TestClient(app) as client:
        assert client.get("/health").status_code == 200
        assert client.get("/internal/v1/assistant/models").status_code == 503


@pytest.mark.parametrize("payload", ['{"mode":"disabled","mode":"enabled"}', '{"limits":{"concurrency":NaN}}'])
def test_duplicate_and_nonfinite_config_is_unavailable(config, payload):
    config[1].write_text(payload)
    assert load_configuration()[-1] == "MISCONFIGURED"


@pytest.mark.asyncio
async def test_cancel_simultaneous_with_headers_closes_received_response(config):
    state = Runtime()
    req = command(state)
    attempt = state.admit(req)
    class Tracked(Bytes):
        closed = False
        async def aclose(self):
            self.closed = True
    stream = Tracked(frames())
    calls = 0
    async def handler(request):
        nonlocal calls
        calls += 1
        attempt["cancelled"].set()
        return httpx.Response(200, headers={"content-type": "text/event-stream"}, stream=stream)
    state.client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    events = [event async for event in state.stream(req, state.model("text"), attempt)]
    assert calls == 1 and stream.closed
    assert '"code":"CANCELLED"' in events[-1] and state.active == 0
    await state.close()
